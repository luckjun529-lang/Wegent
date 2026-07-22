# SPDX-FileCopyrightText: 2025 Weibo, Inc.
#
# SPDX-License-Identifier: Apache-2.0

"""DingTalk knowledge base (wikispace) sync service.

Syncs DingTalk organization knowledge bases through the backend DWS CLI into the
local database with source='wikispace'.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.dingtalk_doc import DingTalkNodeSource, DingtalkSyncedNode
from app.models.user import User
from app.services.dingtalk_doc_service import (
    MAX_NODES_PER_SYNC,
    DingTalkDocService,
)
from app.services.dingtalk_dws_service import dingtalk_dws_service
from shared.telemetry.decorators import (
    add_span_event,
    trace_async,
    trace_sync,
)

logger = logging.getLogger(__name__)

# Source identifier for wikispace nodes
WIKISPACE_SOURCE = DingTalkNodeSource.WIKISPACE

# Wiki space type value for organization knowledge bases.
WIKI_SPACE_TYPE_ORG = "orgWikiSpace"


class DingTalkWikiSpaceService:
    """Service for syncing and querying DingTalk knowledge base (wikispace) nodes."""

    @staticmethod
    @trace_async()
    async def sync_wikispace_nodes(user: User, db: Session) -> dict[str, Any]:
        """Sync DingTalk wikispace nodes from DWS.

        Returns a dict with sync statistics: added, updated, deleted, total,
        dws_nodes_fetched.
        """
        auth_status = await dingtalk_dws_service.auth_status(user.id)
        if not auth_status.get("is_authenticated"):
            raise ValueError("DingTalk is not authorized or authorization has expired")

        all_nodes, traversal_complete = (
            await DingTalkWikiSpaceService._fetch_all_wikispace_nodes(user.id)
        )
        original_count = len(all_nodes)

        if len(all_nodes) > MAX_NODES_PER_SYNC:
            logger.warning(
                "User %s has %d DingTalk wikispace nodes, truncating to %d",
                user.id,
                len(all_nodes),
                MAX_NODES_PER_SYNC,
            )
            all_nodes = all_nodes[:MAX_NODES_PER_SYNC]

        now = datetime.now()
        stats = DingTalkDocService._sync_nodes_to_db(
            user.id,
            all_nodes,
            now,
            db,
            source=WIKISPACE_SOURCE,
            deactivate_missing=traversal_complete
            and original_count <= MAX_NODES_PER_SYNC,
        )
        stats["dws_nodes_fetched"] = original_count
        stats["truncated"] = (
            not traversal_complete or original_count > MAX_NODES_PER_SYNC
        )
        add_span_event(
            "dingtalk.wikispace.sync.completed",
            {
                "dws_nodes_fetched": stats["dws_nodes_fetched"],
            },
        )
        return stats

    # ------------------------------------------------------------------
    # Phase 1 helpers: list knowledge bases via DWS
    # ------------------------------------------------------------------

    @staticmethod
    async def _list_wiki_spaces(user_id: int) -> list[dict[str, Any]]:
        """List all organization knowledge bases via DWS."""
        kb_nodes = await dingtalk_dws_service.list_spaces(user_id, WIKI_SPACE_TYPE_ORG)
        logger.info(
            "DWS wiki space list returned %d knowledge bases",
            len(kb_nodes),
        )
        return kb_nodes

    # ------------------------------------------------------------------
    # Phase 2 helpers: list documents inside each KB via DWS
    # ------------------------------------------------------------------

    @staticmethod
    async def _list_nodes_in_wikispace(
        user_id: int,
        workspace_id: str,
        all_nodes: list[dict[str, Any]],
    ) -> bool:
        """List all nodes in the given KB workspace."""
        fetch_limit = MAX_NODES_PER_SYNC + 1
        cursor: str | None = None
        seen_cursors: set[str] = set()
        traversal_complete = True
        while len(all_nodes) < fetch_limit:
            batch, cursor = await dingtalk_dws_service.list_nodes(
                user_id,
                workspace_id=workspace_id,
                cursor=cursor,
            )

            batch = batch[: fetch_limit - len(all_nodes)]
            for node in batch:
                if not node.get("parentId"):
                    node["parentId"] = workspace_id
                if not node.get("workspaceId"):
                    node["workspaceId"] = workspace_id

            all_nodes.extend(batch)

            for node in batch:
                if DingTalkDocService._extract_node_type(node) != "folder":
                    continue
                node_id = DingTalkDocService._extract_node_id(node)
                if not node_id:
                    continue
                child_complete = await DingTalkDocService._list_nodes_recursive(
                    user_id=user_id,
                    workspace_id=workspace_id,
                    folder_id=node_id,
                    all_nodes=all_nodes,
                    depth=1,
                )
                traversal_complete = traversal_complete and child_complete
                if len(all_nodes) >= fetch_limit:
                    return False

            if not cursor:
                break
            if cursor in seen_cursors:
                raise RuntimeError(
                    f"DWS returned a repeated pagination cursor for workspace {workspace_id}"
                )
            seen_cursors.add(cursor)

        return traversal_complete and len(all_nodes) < fetch_limit

    @staticmethod
    async def _fetch_all_wikispace_nodes(
        user_id: int,
    ) -> tuple[list[dict[str, Any]], bool]:
        """Fetch all documents across all accessible organization knowledge bases."""
        all_nodes: list[dict[str, Any]] = []
        traversal_complete = True
        kb_nodes = await DingTalkWikiSpaceService._list_wiki_spaces(user_id)

        if not kb_nodes:
            logger.warning(
                "DWS wiki space list returned 0 knowledge bases. "
                "Verify DingTalk permissions."
            )
            return all_nodes, traversal_complete

        for kb_node in kb_nodes:
            if len(all_nodes) >= MAX_NODES_PER_SYNC + 1:
                traversal_complete = False
                break
            kb_id = DingTalkDocService._extract_workspace_id(
                kb_node
            ) or DingTalkDocService._extract_node_id(kb_node)
            if not kb_id:
                logger.warning("Skipping KB node with no workspace id")
                continue

            kb_name = kb_node.get("name") or kb_node.get("title") or kb_id
            kb_url = kb_node.get("url") or (
                f"https://alidocs.dingtalk.com/i/spaces/{kb_id}/overview"
            )

            kb_as_folder: dict[str, Any] = {
                **kb_node,
                "nodeId": kb_id,
                "nodeType": "folder",
                "workspaceId": kb_id,
                "name": kb_name,
                "url": kb_url,
            }
            logger.info(
                "Fetching DingTalk KB '%s' (workspace_id=%s)",
                kb_name,
                kb_id,
            )
            space_complete = await DingTalkWikiSpaceService._list_nodes_in_wikispace(
                user_id=user_id,
                workspace_id=kb_id,
                all_nodes=all_nodes,
            )
            traversal_complete = traversal_complete and space_complete
            if len(all_nodes) < MAX_NODES_PER_SYNC + 1:
                all_nodes.append(kb_as_folder)

        unique_nodes = DingTalkWikiSpaceService._dedupe_nodes_by_id(all_nodes)

        logger.info(
            "DWS WikiSpace sync fetched %d total nodes across %d knowledge bases",
            len(unique_nodes),
            len(kb_nodes),
        )
        return unique_nodes, traversal_complete

    @staticmethod
    def _dedupe_nodes_by_id(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """De-duplicate nodes by node ID while preserving the existing API."""
        return DingTalkDocService._dedupe_nodes_by_id(nodes)

    # ------------------------------------------------------------------
    # Read helpers
    # ------------------------------------------------------------------

    @staticmethod
    @trace_sync()
    def get_wikispace_nodes(user_id: int, db: Session) -> list[DingtalkSyncedNode]:
        """Get all active DingTalk wikispace nodes for a user."""
        return (
            db.query(DingtalkSyncedNode)
            .filter(
                DingtalkSyncedNode.user_id == user_id,
                DingtalkSyncedNode.source == WIKISPACE_SOURCE.value,
                DingtalkSyncedNode.is_active == True,  # noqa: E712
            )
            .order_by(DingtalkSyncedNode.node_type, DingtalkSyncedNode.name)
            .all()
        )

    @staticmethod
    @trace_async()
    async def get_sync_status(user: User, db: Session) -> dict[str, Any]:
        """Get sync status for a user's DingTalk wikispace nodes."""
        auth_status = await dingtalk_dws_service.auth_status(user.id)

        last_synced = (
            db.query(DingtalkSyncedNode.last_synced_at)
            .filter(
                DingtalkSyncedNode.user_id == user.id,
                DingtalkSyncedNode.source == WIKISPACE_SOURCE.value,
                DingtalkSyncedNode.is_active == True,  # noqa: E712
            )
            .order_by(DingtalkSyncedNode.last_synced_at.desc())
            .first()
        )

        total = (
            db.query(DingtalkSyncedNode)
            .filter(
                DingtalkSyncedNode.user_id == user.id,
                DingtalkSyncedNode.source == WIKISPACE_SOURCE.value,
                DingtalkSyncedNode.is_active == True,  # noqa: E712
            )
            .count()
        )

        return {
            "last_synced_at": last_synced[0] if last_synced else None,
            "total_nodes": total,
            "is_configured": auth_status["is_authenticated"],
            "is_authenticated": auth_status["is_authenticated"],
            "auth_status": auth_status["auth_status"],
        }
