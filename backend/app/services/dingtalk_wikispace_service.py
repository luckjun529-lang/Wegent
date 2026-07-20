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
from urllib.parse import urlparse, urlunparse

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

# wiki space type value for org-level KBs (as opposed to "myWikiSpace")
WIKI_SPACE_TYPE_ORG = "orgWikiSpace"


def _sanitize_url_for_telemetry(url: str) -> str:
    """Sanitize a URL by removing userinfo and query string for safe telemetry logging.

    This prevents credentials or sensitive query parameters from being exposed
    in telemetry spans and logs.

    Args:
        url: The URL to sanitize.

    Returns:
        A sanitized URL containing only scheme, host, port, and path.
    """
    try:
        parsed = urlparse(url)
        netloc = parsed.hostname or ""
        if parsed.port:
            netloc = f"{netloc}:{parsed.port}"

        sanitized = urlunparse(
            (
                parsed.scheme,
                netloc,
                parsed.path,
                "",  # params (deprecated, always empty)
                "",  # query - removed for security
                "",  # fragment - removed for security
            )
        )
        return sanitized
    except Exception:
        # If URL parsing fails, return a placeholder to avoid exposing raw URL
        return "<invalid-url>"


class DingTalkWikiSpaceService:
    """Service for syncing and querying DingTalk knowledge base (wikispace) nodes."""

    @staticmethod
    @trace_sync()
    def get_user_wikispace_mcp_url(user: User) -> str | None:
        """Legacy MCP URL accessor kept for compatibility with old callers."""
        return None

    @staticmethod
    @trace_sync()
    def is_configured(user: User) -> bool:
        """DWS is backend-managed; authorization is checked by DWS auth status."""
        return True

    @staticmethod
    @trace_async()
    async def sync_wikispace_nodes(user: User, db: Session) -> dict[str, Any]:
        """Sync DingTalk wikispace nodes from DWS.

        Returns a dict with sync statistics: added, updated, deleted, total,
        mcp_nodes_fetched.
        """
        auth_status = await dingtalk_dws_service.auth_status(user.id)
        if not auth_status.get("is_authenticated"):
            raise ValueError("DingTalk is not authorized or authorization has expired")

        all_nodes = await DingTalkWikiSpaceService._fetch_all_wikispace_nodes(user.id)
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
            user.id, all_nodes, now, db, source=WIKISPACE_SOURCE
        )
        stats["mcp_nodes_fetched"] = original_count
        add_span_event(
            "dingtalk.wikispace.sync.completed",
            {
                "mcp_nodes_fetched": stats["mcp_nodes_fetched"],
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
    ) -> None:
        """List all nodes in the given KB workspace."""
        cursor: str | None = None
        while True:
            batch, cursor = await dingtalk_dws_service.list_nodes(
                user_id,
                workspace_id=workspace_id,
                cursor=cursor,
            )

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
                await DingTalkDocService._list_nodes_recursive(
                    user_id=user_id,
                    workspace_id=workspace_id,
                    folder_id=node_id,
                    all_nodes=all_nodes,
                    depth=1,
                )

            if not cursor:
                break

    @staticmethod
    async def _fetch_all_wikispace_nodes(user_id: int) -> list[dict[str, Any]]:
        """Fetch all documents across all accessible organization knowledge bases."""
        all_nodes: list[dict[str, Any]] = []
        kb_nodes = await DingTalkWikiSpaceService._list_wiki_spaces(user_id)

        if not kb_nodes:
            logger.warning(
                "DWS wiki space list returned 0 knowledge bases. "
                "Verify DingTalk permissions."
            )
            return all_nodes

        for kb_node in kb_nodes:
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
            try:
                await DingTalkWikiSpaceService._list_nodes_in_wikispace(
                    user_id=user_id,
                    workspace_id=kb_id,
                    all_nodes=all_nodes,
                )
                all_nodes.append(kb_as_folder)
            except Exception as exc:
                logger.error(
                    "Failed to list nodes in KB '%s' (id=%s): %s",
                    kb_name,
                    kb_id,
                    exc,
                )

        unique_nodes = DingTalkWikiSpaceService._dedupe_nodes_by_id(all_nodes)

        logger.info(
            "DWS WikiSpace sync fetched %d total nodes across %d knowledge bases",
            len(unique_nodes),
            len(kb_nodes),
        )
        return unique_nodes

    @staticmethod
    def _dedupe_nodes_by_id(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """De-duplicate nodes by nodeId while keeping the most complete entry.

        Completeness is determined by counting the number of fields with
        meaningful (non-None, non-empty-string) values.
        """

        def _completeness(node: dict[str, Any]) -> int:
            return sum(1 for v in node.values() if v is not None and v != "")

        seen: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        for node in nodes:
            node_id = (
                node.get("nodeId")
                or node.get("workspaceId")
                or node.get("id")
                or node.get("dingtalk_node_id")
            )
            if not node_id:
                continue

            if node_id not in seen:
                seen[node_id] = node
                order.append(node_id)
                continue

            existing = seen[node_id]
            if _completeness(node) > _completeness(existing):
                seen[node_id] = node

        return [seen[node_id] for node_id in order]

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
    @trace_sync()
    def get_sync_status(user: User, db: Session) -> dict[str, Any]:
        """Get sync status for a user's DingTalk wikispace nodes."""
        is_configured = DingTalkWikiSpaceService.is_configured(user)

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
            "is_configured": is_configured,
        }
