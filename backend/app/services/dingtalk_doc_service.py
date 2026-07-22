# SPDX-FileCopyrightText: 2025 Weibo, Inc.
#
# SPDX-License-Identifier: Apache-2.0

"""DingTalk document sync service.

Syncs DingTalk document nodes through the backend DWS CLI into the local database.
The DWS login state is isolated per Wegent user by DingTalkDwsService.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.dingtalk_doc import DingTalkNodeSource, DingtalkSyncedNode
from app.models.user import User
from app.services.dingtalk_dws_service import DwsCommandError, dingtalk_dws_service

logger = logging.getLogger(__name__)

# Maximum recursion depth for folder traversal
MAX_RECURSION_DEPTH = 10

# Maximum nodes to sync per user (safety limit)
MAX_NODES_PER_SYNC = 5000

DOCS_SOURCE = DingTalkNodeSource.DOCS


class DingTalkDocService:
    """Service for syncing and querying DingTalk document nodes."""

    @staticmethod
    async def sync_dingtalk_docs(user: User, db: Session) -> dict[str, Any]:
        """Sync DingTalk document nodes from DWS.

        Returns a dict with sync statistics: added, updated, deleted, total.
        """
        auth_status = await dingtalk_dws_service.auth_status(user.id)
        if not auth_status.get("is_authenticated"):
            raise ValueError("DingTalk is not authorized or authorization has expired")

        all_nodes, traversal_complete = await DingTalkDocService._fetch_all_nodes(
            user.id
        )
        original_count = len(all_nodes)

        if len(all_nodes) > MAX_NODES_PER_SYNC:
            logger.warning(
                "User %s has %d DingTalk nodes, truncating to %d",
                user.id,
                len(all_nodes),
                MAX_NODES_PER_SYNC,
            )
            all_nodes = all_nodes[:MAX_NODES_PER_SYNC]

        # Sync to database - use local time (no timezone) consistent with created_at
        now = datetime.now()
        stats = DingTalkDocService._sync_nodes_to_db(
            user.id,
            all_nodes,
            now,
            db,
            source=DOCS_SOURCE,
            deactivate_missing=traversal_complete
            and original_count <= MAX_NODES_PER_SYNC,
        )
        stats["dws_nodes_fetched"] = original_count
        stats["truncated"] = (
            not traversal_complete or original_count > MAX_NODES_PER_SYNC
        )

        return stats

    @staticmethod
    async def _fetch_all_nodes(
        user_id: int,
    ) -> tuple[list[dict[str, Any]], bool]:
        """Fetch all personal-document nodes from DWS myWikiSpace."""
        all_nodes: list[dict[str, Any]] = []
        traversal_complete = True
        spaces = await dingtalk_dws_service.list_spaces(user_id, "myWikiSpace")
        for space in spaces:
            if len(all_nodes) >= MAX_NODES_PER_SYNC + 1:
                traversal_complete = False
                break
            workspace_id = DingTalkDocService._extract_workspace_id(space)
            if not workspace_id:
                logger.warning("Skipping DingTalk personal space without workspace id")
                continue
            space_complete = await DingTalkDocService._list_nodes_recursive(
                user_id=user_id,
                workspace_id=workspace_id,
                folder_id=None,
                all_nodes=all_nodes,
                depth=0,
            )
            traversal_complete = traversal_complete and space_complete
        return DingTalkDocService._dedupe_nodes_by_id(all_nodes), traversal_complete

    @staticmethod
    async def _list_nodes_recursive(
        user_id: int,
        workspace_id: str,
        folder_id: str | None,
        all_nodes: list[dict[str, Any]],
        depth: int,
        visited_folders: set[tuple[str, str]] | None = None,
    ) -> bool:
        """Recursively list DWS wiki nodes under a workspace/folder."""
        if depth >= MAX_RECURSION_DEPTH:
            logger.warning(
                "Max recursion depth %d reached at folder %s",
                depth,
                folder_id,
            )
            return False

        fetch_limit = MAX_NODES_PER_SYNC + 1
        if len(all_nodes) >= fetch_limit:
            return False

        if visited_folders is None:
            visited_folders = set()
        if folder_id is not None:
            folder_key = (workspace_id, folder_id)
            if folder_key in visited_folders:
                logger.warning("Skipping repeated DingTalk folder %s", folder_id)
                return True
            visited_folders.add(folder_key)

        cursor = None
        seen_cursors: set[str] = set()
        traversal_complete = True
        while True:
            nodes_data, cursor = await dingtalk_dws_service.list_nodes(
                user_id,
                workspace_id=workspace_id,
                folder_id=folder_id,
                cursor=cursor,
            )

            remaining = fetch_limit - len(all_nodes)
            nodes_data = nodes_data[:remaining]
            for node in nodes_data:
                if folder_id and not node.get("parentId"):
                    node["parentId"] = folder_id
                if workspace_id and not node.get("workspaceId"):
                    node["workspaceId"] = workspace_id

            all_nodes.extend(nodes_data)

            for node in nodes_data:
                if DingTalkDocService._extract_node_type(node) == "folder":
                    node_id = DingTalkDocService._extract_node_id(node)
                    if not node_id:
                        continue
                    child_complete = await DingTalkDocService._list_nodes_recursive(
                        user_id=user_id,
                        workspace_id=workspace_id,
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
                raise DwsCommandError(
                    f"DWS returned a repeated pagination cursor for workspace {workspace_id}"
                )
            seen_cursors.add(cursor)

            if len(all_nodes) >= fetch_limit:
                return False

        return traversal_complete

    @staticmethod
    def _extract_workspace_id(data: dict[str, Any]) -> str:
        return str(
            data.get("workspaceId")
            or data.get("workspace_id")
            or data.get("spaceId")
            or data.get("space_id")
            or data.get("id")
            or ""
        )

    @staticmethod
    def _extract_node_id(data: dict[str, Any]) -> str:
        return str(
            data.get("nodeId")
            or data.get("node_id")
            or data.get("fileId")
            or data.get("file_id")
            or data.get("dentryUuid")
            or data.get("dingtalk_node_id")
            or data.get("id")
            or ""
        )

    @staticmethod
    def _extract_node_type(data: dict[str, Any]) -> str:
        if data.get("isFolder") is True or data.get("is_folder") is True:
            return "folder"

        raw_type = str(
            data.get("nodeType")
            or data.get("node_type")
            or data.get("dentryType")
            or data.get("dentry_type")
            or data.get("type")
            or "doc"
        ).lower()
        if raw_type in {"folder", "directory", "dir", "1"}:
            return "folder"
        if raw_type in {"file", "0"}:
            return "file"
        return "doc"

    @staticmethod
    def _dedupe_nodes_by_id(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """De-duplicate nodes while keeping the most complete payload."""

        def completeness(node: dict[str, Any]) -> int:
            return sum(
                1 for value in node.values() if value is not None and value != ""
            )

        seen: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        for node in nodes:
            node_id = DingTalkDocService._extract_node_id(node)
            if not node_id:
                continue
            if node_id not in seen:
                seen[node_id] = node
                order.append(node_id)
                continue
            if completeness(node) > completeness(seen[node_id]):
                seen[node_id] = node
        return [seen[node_id] for node_id in order]

    @staticmethod
    def _normalize_source(source: DingTalkNodeSource | str) -> str:
        """Normalize a DingTalk node source into its persisted string value."""
        if isinstance(source, DingTalkNodeSource):
            return source.value

        try:
            return DingTalkNodeSource(source).value
        except ValueError as exc:
            raise ValueError(f"Unsupported DingTalk node source: {source}") from exc

    @staticmethod
    def _sync_nodes_to_db(
        user_id: int,
        nodes: list[dict[str, Any]],
        sync_time: datetime,
        db: Session,
        source: DingTalkNodeSource = DOCS_SOURCE,
        *,
        deactivate_missing: bool = True,
    ) -> dict[str, Any]:
        """Sync fetched nodes to the database.

        Compares with existing records and performs add/update/delete operations.
        Only operates on nodes with the given source value.
        """
        source_value = DingTalkDocService._normalize_source(source)
        added = 0
        updated = 0
        deleted = 0

        nodes = DingTalkDocService._dedupe_nodes_by_id(nodes)

        # Build a map of current DingTalk node IDs
        dingtalk_node_ids = set()
        for node_data in nodes:
            node_id = DingTalkDocService._extract_node_id(node_data)
            if not node_id:
                continue
            dingtalk_node_ids.add(node_id)

        # Mark nodes no longer in DingTalk as inactive (filter by source)
        existing_active = (
            db.query(DingtalkSyncedNode)
            .filter(
                DingtalkSyncedNode.user_id == user_id,
                DingtalkSyncedNode.source == source_value,
                DingtalkSyncedNode.is_active == True,  # noqa: E712
            )
            .all()
        )

        if deactivate_missing:
            for existing in existing_active:
                if existing.dingtalk_node_id not in dingtalk_node_ids:
                    existing.is_active = False
                    existing.updated_at = sync_time
                    deleted += 1

        # Collect all non-empty node IDs for a single batch lookup (avoids N+1 queries)
        node_ids = [
            DingTalkDocService._extract_node_id(node_data)
            for node_data in nodes
            if DingTalkDocService._extract_node_id(node_data)
        ]
        existing_nodes_map: dict[str, DingtalkSyncedNode] = {}
        if node_ids:
            existing_rows = (
                db.query(DingtalkSyncedNode)
                .filter(
                    DingtalkSyncedNode.user_id == user_id,
                    DingtalkSyncedNode.source == source_value,
                    DingtalkSyncedNode.dingtalk_node_id.in_(node_ids),
                )
                .all()
            )
            existing_nodes_map = {row.dingtalk_node_id: row for row in existing_rows}

        # Upsert nodes from DingTalk
        for node_data in nodes:
            node_id = DingTalkDocService._extract_node_id(node_data)
            if not node_id:
                continue

            name = (
                node_data.get("name")
                or node_data.get("fileName")
                or node_data.get("file_name")
                or node_data.get("title")
                or "Untitled"
            )
            node_type_raw = DingTalkDocService._extract_node_type(node_data)

            # Map node type
            if node_type_raw == "folder":
                node_type = "folder"
            elif node_type_raw == "file":
                node_type = "file"
            else:
                node_type = "doc"

            # Build document URL
            doc_url = node_data.get("url") or node_data.get("docUrl") or ""
            if not doc_url:
                doc_url = f"https://alidocs.dingtalk.com/i/nodes/{node_id}"

            parent_node_id = (
                node_data.get("parentId")
                or node_data.get("parent_id")
                or node_data.get("parentNodeId")
                or node_data.get("parentDentryUuid")
                or ""
            )
            workspace_id = (
                node_data.get("workspaceId") or node_data.get("workspace_id") or ""
            )
            content_type = (
                node_data.get("contentType")
                or node_data.get("content_type")
                or node_data.get("extension")
                or node_data.get("fileExtension")
                or ""
            )
            content_updated_at = DingTalkDocService._parse_update_time(
                node_data.get("updateTime")
                or node_data.get("modifyTime")
                or node_data.get("modifiedTime")
                or node_data.get("updated_at"),
                sync_time,
            )

            # Look up existing node from pre-fetched map (avoids per-node DB query)
            existing = existing_nodes_map.get(node_id)

            if existing:
                # Update existing node
                changed = False
                if existing.name != name:
                    existing.name = name
                    changed = True
                if existing.doc_url != doc_url:
                    existing.doc_url = doc_url
                    changed = True
                if existing.parent_node_id != parent_node_id:
                    existing.parent_node_id = parent_node_id
                    changed = True
                if existing.node_type != node_type:
                    existing.node_type = node_type
                    changed = True
                if existing.workspace_id != workspace_id:
                    existing.workspace_id = workspace_id
                    changed = True
                if existing.content_type != content_type:
                    existing.content_type = content_type
                    changed = True
                if existing.content_updated_at != content_updated_at:
                    existing.content_updated_at = content_updated_at
                    changed = True
                if not existing.is_active:
                    existing.is_active = True
                    changed = True

                if changed:
                    existing.last_synced_at = sync_time
                    existing.updated_at = sync_time
                    updated += 1
                else:
                    existing.last_synced_at = sync_time
            else:
                # Create new node
                new_node = DingtalkSyncedNode(
                    user_id=user_id,
                    dingtalk_node_id=node_id,
                    name=name,
                    doc_url=doc_url,
                    parent_node_id=parent_node_id,
                    node_type=node_type,
                    workspace_id=workspace_id,
                    content_type=content_type,
                    content_updated_at=content_updated_at,
                    is_active=True,
                    last_synced_at=sync_time,
                    source=source_value,
                )
                db.add(new_node)
                added += 1

        try:
            db.commit()
        except Exception:
            db.rollback()
            logger.exception(
                "Failed to commit synced nodes for user %s (source=%s)",
                user_id,
                source_value,
            )
            raise

        total = (
            db.query(DingtalkSyncedNode)
            .filter(
                DingtalkSyncedNode.user_id == user_id,
                DingtalkSyncedNode.source == source_value,
                DingtalkSyncedNode.is_active == True,  # noqa: E712
            )
            .count()
        )

        return {
            "added": added,
            "updated": updated,
            "deleted": deleted,
            "total": total,
            "sync_time": sync_time,
        }

    @staticmethod
    def _parse_update_time(update_time: Any, fallback: datetime) -> datetime:
        """Parse updateTime from list_nodes response into a datetime (local time).

        DingTalk may return updateTime as a Unix timestamp (int/float,
        in seconds or milliseconds) or as an ISO 8601 string. The result is
        converted to local time (no tzinfo) to be consistent with created_at.
        Falls back to the provided fallback datetime if parsing fails or absent.
        """
        if update_time is None:
            return fallback
        try:
            if isinstance(update_time, (int, float)):
                # Treat values > 1e10 as milliseconds, otherwise seconds
                ts = float(update_time)
                if ts > 1e10:
                    ts = ts / 1000.0
                # fromtimestamp without tz converts to local time directly
                return datetime.fromtimestamp(ts)
            if isinstance(update_time, str):
                # Try ISO 8601 parse, then convert to local time
                dt = datetime.fromisoformat(update_time.replace("Z", "+00:00"))
                if dt.tzinfo is not None:
                    # Convert to local time and strip tzinfo
                    dt = dt.astimezone().replace(tzinfo=None)
                return dt
        except (ValueError, OSError, OverflowError):
            logger.warning("Failed to parse updateTime value: %r", update_time)
        return fallback

    @staticmethod
    def get_dingtalk_docs(user_id: int, db: Session) -> list[DingtalkSyncedNode]:
        """Get all active DingTalk document nodes for a user."""
        return (
            db.query(DingtalkSyncedNode)
            .filter(
                DingtalkSyncedNode.user_id == user_id,
                DingtalkSyncedNode.source == DOCS_SOURCE.value,
                DingtalkSyncedNode.is_active == True,  # noqa: E712
            )
            .order_by(DingtalkSyncedNode.node_type, DingtalkSyncedNode.name)
            .all()
        )

    @staticmethod
    async def get_sync_status(user: User, db: Session) -> dict[str, Any]:
        """Get sync status for a user's DingTalk documents."""
        auth_status = await dingtalk_dws_service.auth_status(user.id)

        last_synced = (
            db.query(DingtalkSyncedNode.last_synced_at)
            .filter(
                DingtalkSyncedNode.user_id == user.id,
                DingtalkSyncedNode.source == DOCS_SOURCE.value,
                DingtalkSyncedNode.is_active == True,  # noqa: E712
            )
            .order_by(DingtalkSyncedNode.last_synced_at.desc())
            .first()
        )

        total = (
            db.query(DingtalkSyncedNode)
            .filter(
                DingtalkSyncedNode.user_id == user.id,
                DingtalkSyncedNode.source == DOCS_SOURCE.value,
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

    @staticmethod
    def delete_synced_node(
        node_id: int,
        user_id: int,
        db: Session,
        source: DingTalkNodeSource | None = DOCS_SOURCE,
    ) -> bool:
        """Delete a synced document node (local cache only).

        Args:
            node_id: The database ID of the node to delete.
            user_id: The user ID who owns the node.
            db: Database session.
            source: Source filter for the node (docs by default).
                    Pass None explicitly only when cross-source deletion is intended.

        Returns:
            True if the node was found and deleted, False otherwise.
        """
        filters = [
            DingtalkSyncedNode.id == node_id,
            DingtalkSyncedNode.user_id == user_id,
        ]
        if source is not None:
            filters.append(
                DingtalkSyncedNode.source
                == DingTalkDocService._normalize_source(source)
            )

        node = db.query(DingtalkSyncedNode).filter(*filters).first()
        if not node:
            return False

        db.delete(node)
        db.commit()
        return True


dingtalk_doc_service = DingTalkDocService()
