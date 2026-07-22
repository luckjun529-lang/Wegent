# SPDX-FileCopyrightText: 2026 Weibo, Inc.
#
# SPDX-License-Identifier: Apache-2.0

"""DingTalk team-file sync service backed by DWS drive commands."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.dingtalk_doc import DingTalkNodeSource, DingtalkSyncedNode
from app.models.user import User
from app.services.dingtalk_doc_service import (
    MAX_NODES_PER_SYNC,
    MAX_RECURSION_DEPTH,
    DingTalkDocService,
)
from app.services.dingtalk_dws_service import DwsCommandError, dingtalk_dws_service
from shared.telemetry.decorators import add_span_event, trace_async, trace_sync

logger = logging.getLogger(__name__)

TEAM_FILES_SOURCE = DingTalkNodeSource.TEAM_FILES
TEAM_SPACE_TYPE = "orgSpace"


class DingTalkTeamFilesService:
    """Sync and query DingTalk team drive spaces for a Wegent user."""

    @staticmethod
    @trace_async()
    async def sync_team_files(user: User, db: Session) -> dict[str, Any]:
        """Sync all accessible DingTalk team spaces into the local node cache."""
        auth_status = await dingtalk_dws_service.auth_status(user.id)
        if not auth_status.get("is_authenticated"):
            raise ValueError("DingTalk is not authorized or authorization has expired")

        all_nodes, traversal_complete = (
            await DingTalkTeamFilesService._fetch_all_team_file_nodes(user.id)
        )
        original_count = len(all_nodes)
        if original_count > MAX_NODES_PER_SYNC:
            logger.warning(
                "User %s has more than %d DingTalk team-file nodes; truncating",
                user.id,
                MAX_NODES_PER_SYNC,
            )
            all_nodes = all_nodes[:MAX_NODES_PER_SYNC]

        now = datetime.now()
        stats = DingTalkDocService._sync_nodes_to_db(
            user.id,
            all_nodes,
            now,
            db,
            source=TEAM_FILES_SOURCE,
            deactivate_missing=traversal_complete
            and original_count <= MAX_NODES_PER_SYNC,
        )
        stats["dws_nodes_fetched"] = original_count
        stats["truncated"] = (
            not traversal_complete or original_count > MAX_NODES_PER_SYNC
        )
        add_span_event(
            "dingtalk.team_files.sync.completed",
            {"dws_nodes_fetched": original_count},
        )
        return stats

    @staticmethod
    async def _list_team_spaces(user_id: int) -> list[dict[str, Any]]:
        """List DingTalk enterprise drive spaces visible to the current user."""
        spaces = await dingtalk_dws_service.list_spaces(user_id, TEAM_SPACE_TYPE)
        logger.info("DWS returned %d DingTalk team spaces", len(spaces))
        return spaces

    @staticmethod
    async def _list_nodes_recursive(
        *,
        user_id: int,
        space_id: str,
        parent_node_id: str,
        folder_id: str | None,
        all_nodes: list[dict[str, Any]],
        depth: int,
        visited_folders: set[tuple[str, str]] | None = None,
    ) -> bool:
        """Recursively list one DingTalk drive directory with pagination."""
        fetch_limit = MAX_NODES_PER_SYNC + 1
        if depth >= MAX_RECURSION_DEPTH or len(all_nodes) >= fetch_limit:
            return False

        if visited_folders is None:
            visited_folders = set()
        if folder_id is not None:
            folder_key = (space_id, folder_id)
            if folder_key in visited_folders:
                logger.warning(
                    "Skipping repeated DingTalk team-file folder %s in space %s",
                    folder_id,
                    space_id,
                )
                return True
            visited_folders.add(folder_key)

        cursor: str | None = None
        seen_cursors: set[str] = set()
        traversal_complete = True
        while len(all_nodes) < fetch_limit:
            batch, cursor = await dingtalk_dws_service.list_drive_nodes(
                user_id,
                space_id=space_id,
                folder_id=folder_id,
                cursor=cursor,
            )
            normalized_batch = [
                DingTalkTeamFilesService._normalize_drive_node(
                    node,
                    space_id=space_id,
                    parent_node_id=parent_node_id,
                )
                for node in batch
            ]
            remaining = fetch_limit - len(all_nodes)
            normalized_batch = normalized_batch[:remaining]
            all_nodes.extend(normalized_batch)

            for node in normalized_batch:
                if DingTalkDocService._extract_node_type(node) != "folder":
                    continue
                node_id = DingTalkDocService._extract_node_id(node)
                if not node_id:
                    continue
                child_complete = await DingTalkTeamFilesService._list_nodes_recursive(
                    user_id=user_id,
                    space_id=space_id,
                    parent_node_id=node_id,
                    folder_id=node_id,
                    all_nodes=all_nodes,
                    depth=depth + 1,
                    visited_folders=visited_folders,
                )
                traversal_complete = traversal_complete and child_complete
                if len(all_nodes) >= fetch_limit:
                    return False

            if not cursor:
                break
            if cursor in seen_cursors:
                raise RuntimeError(
                    f"DWS returned a repeated pagination cursor for space {space_id}"
                )
            seen_cursors.add(cursor)

        return traversal_complete and len(all_nodes) < fetch_limit

    @staticmethod
    def _normalize_drive_node(
        node: dict[str, Any],
        *,
        space_id: str,
        parent_node_id: str,
    ) -> dict[str, Any]:
        normalized = dict(node)
        normalized["parentId"] = parent_node_id
        normalized["workspaceId"] = space_id
        normalized["nodeType"] = DingTalkDocService._extract_node_type(normalized)
        return normalized

    @staticmethod
    async def _fetch_all_team_file_nodes(
        user_id: int,
    ) -> tuple[list[dict[str, Any]], bool]:
        """Fetch team-space roots and every file below them."""
        all_nodes: list[dict[str, Any]] = []
        traversal_complete = True
        spaces = await DingTalkTeamFilesService._list_team_spaces(user_id)
        for space in spaces:
            if len(all_nodes) >= MAX_NODES_PER_SYNC + 1:
                traversal_complete = False
                break
            space_id = DingTalkTeamFilesService._extract_space_id(space)
            if not space_id:
                logger.warning("Skipping DingTalk team space without spaceId")
                continue
            root_node = DingTalkTeamFilesService._build_space_root(space, space_id)
            root_node_id = DingTalkDocService._extract_node_id(root_node)
            space_start = len(all_nodes)
            all_nodes.append(root_node)
            try:
                space_complete = await DingTalkTeamFilesService._list_nodes_recursive(
                    user_id=user_id,
                    space_id=space_id,
                    parent_node_id=root_node_id,
                    folder_id=None,
                    all_nodes=all_nodes,
                    depth=0,
                    visited_folders={(space_id, root_node_id)},
                )
            except DwsCommandError as exc:
                if not dingtalk_dws_service.is_access_denied_error(exc):
                    raise
                del all_nodes[space_start:]
                logger.info(
                    "Skipping inaccessible DingTalk team space %s",
                    space_id,
                )
                continue
            traversal_complete = traversal_complete and space_complete
        return DingTalkDocService._dedupe_nodes_by_id(all_nodes), traversal_complete

    @staticmethod
    def _extract_space_id(space: dict[str, Any]) -> str:
        return str(
            space.get("spaceId") or space.get("space_id") or space.get("id") or ""
        )

    @staticmethod
    def _build_space_root(space: dict[str, Any], space_id: str) -> dict[str, Any]:
        root_id = str(
            space.get("rootFolderId")
            or space.get("root_folder_id")
            or f"team-space-{space_id}"
        )
        return {
            **space,
            "nodeId": root_id,
            "nodeType": "folder",
            "workspaceId": space_id,
            "parentId": "",
            "name": space.get("spaceName") or space.get("name") or space_id,
        }

    @staticmethod
    @trace_sync()
    def get_team_files(user_id: int, db: Session) -> list[DingtalkSyncedNode]:
        """Return active team-file nodes owned by the current Wegent user."""
        return (
            db.query(DingtalkSyncedNode)
            .filter(
                DingtalkSyncedNode.user_id == user_id,
                DingtalkSyncedNode.source == TEAM_FILES_SOURCE.value,
                DingtalkSyncedNode.is_active == True,  # noqa: E712
            )
            .order_by(DingtalkSyncedNode.node_type, DingtalkSyncedNode.name)
            .all()
        )

    @staticmethod
    @trace_async()
    async def get_sync_status(user: User, db: Session) -> dict[str, Any]:
        """Return last sync time and active team-file node count."""
        auth_status = await dingtalk_dws_service.auth_status(user.id)
        active = db.query(DingtalkSyncedNode).filter(
            DingtalkSyncedNode.user_id == user.id,
            DingtalkSyncedNode.source == TEAM_FILES_SOURCE.value,
            DingtalkSyncedNode.is_active == True,  # noqa: E712
        )
        last_synced = (
            active.with_entities(DingtalkSyncedNode.last_synced_at)
            .order_by(DingtalkSyncedNode.last_synced_at.desc())
            .first()
        )
        return {
            "last_synced_at": last_synced[0] if last_synced else None,
            "total_nodes": active.count(),
            "is_configured": auth_status["is_authenticated"],
            "is_authenticated": auth_status["is_authenticated"],
            "auth_status": auth_status["auth_status"],
        }


dingtalk_team_files_service = DingTalkTeamFilesService()
