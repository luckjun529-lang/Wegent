# SPDX-FileCopyrightText: 2025 Weibo, Inc.
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for DingTalkWikiSpaceService DWS-based sync."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.dingtalk_doc import DingTalkNodeSource, DingtalkSyncedNode
from app.services.dingtalk_doc_service import DingTalkDocService
from app.services.dingtalk_wikispace_service import (
    DingTalkWikiSpaceService,
    _sanitize_url_for_telemetry,
)


class TestSanitizeUrlForTelemetry:
    """Tests for URL sanitization used in telemetry."""

    def test_preserves_port_while_stripping_credentials_and_query(self) -> None:
        """Sanitized URL keeps host and port but removes credentials and query."""
        sanitized = _sanitize_url_for_telemetry(
            "https://user:secret@mcp.example.com:8443/api?token=secret#frag"
        )

        assert sanitized == "https://mcp.example.com:8443/api"

    def test_returns_invalid_placeholder_on_parse_failure(self) -> None:
        """Invalid URLs never echo the raw input back to telemetry."""
        sanitized = _sanitize_url_for_telemetry("http://[invalid-url")

        assert sanitized == "<invalid-url>"


class TestListWikiSpaces:
    """Tests for DingTalkWikiSpaceService._list_wiki_spaces."""

    @pytest.mark.asyncio
    async def test_returns_org_wiki_spaces_from_dws(self) -> None:
        """Organization knowledge bases are listed through DWS orgWikiSpace."""
        kb_data = [
            {"workspaceId": "WS001", "name": "KB One"},
            {"workspaceId": "WS002", "name": "KB Two"},
        ]

        with patch(
            "app.services.dingtalk_wikispace_service.dingtalk_dws_service.list_spaces",
            new=AsyncMock(return_value=kb_data),
        ) as mock_list_spaces:
            result = await DingTalkWikiSpaceService._list_wiki_spaces(user_id=9)

        assert result == kb_data
        mock_list_spaces.assert_awaited_once_with(9, "orgWikiSpace")


class TestListNodesInWikispace:
    """Tests for DingTalkWikiSpaceService._list_nodes_in_wikispace."""

    @pytest.mark.asyncio
    async def test_recurses_into_root_folder_and_continues_root_pagination(
        self,
    ) -> None:
        """Lists first page, recurses into folders, then continues pagination."""
        calls: list[str | None] = []

        async def list_nodes(
            user_id: int,
            *,
            workspace_id: str,
            folder_id: str | None = None,
            cursor: str | None = None,
        ) -> tuple[list[dict], str | None]:
            calls.append(cursor)
            if cursor is None:
                return (
                    [
                        {
                            "nodeId": "folder-1",
                            "nodeType": "folder",
                            "workspaceId": "WS1",
                        },
                        {"nodeId": "doc-1", "nodeType": "doc", "workspaceId": "WS1"},
                    ],
                    "page-2",
                )
            return (
                [
                    {
                        "nodeId": "folder-2",
                        "nodeType": "folder",
                        "workspaceId": "WS1",
                    },
                    {"nodeId": "doc-2", "nodeType": "doc", "workspaceId": "WS1"},
                ],
                None,
            )

        all_nodes: list[dict] = []
        with (
            patch(
                "app.services.dingtalk_wikispace_service.dingtalk_dws_service.list_nodes",
                new=AsyncMock(side_effect=list_nodes),
            ),
            patch.object(
                DingTalkDocService,
                "_list_nodes_recursive",
                new=AsyncMock(),
            ) as mock_recursive,
        ):
            await DingTalkWikiSpaceService._list_nodes_in_wikispace(
                user_id=9,
                workspace_id="WS1",
                all_nodes=all_nodes,
            )

        assert calls == [None, "page-2"]
        assert [node["nodeId"] for node in all_nodes] == [
            "folder-1",
            "doc-1",
            "folder-2",
            "doc-2",
        ]
        assert all(node["parentId"] == "WS1" for node in all_nodes)
        assert mock_recursive.await_count == 2
        assert mock_recursive.await_args_list[0].kwargs["folder_id"] == "folder-1"
        assert mock_recursive.await_args_list[1].kwargs["folder_id"] == "folder-2"

    @pytest.mark.asyncio
    async def test_skips_folder_recursion_when_node_id_missing(self) -> None:
        """Folder entries without nodeId are not passed to recursive traversal."""
        all_nodes: list[dict] = []

        with (
            patch(
                "app.services.dingtalk_wikispace_service.dingtalk_dws_service.list_nodes",
                new=AsyncMock(
                    return_value=(
                        [
                            {"nodeType": "folder", "workspaceId": "WS1"},
                            {
                                "nodeId": "doc-1",
                                "nodeType": "doc",
                                "workspaceId": "WS1",
                            },
                        ],
                        None,
                    )
                ),
            ),
            patch.object(
                DingTalkDocService,
                "_list_nodes_recursive",
                new=AsyncMock(),
            ) as mock_recursive,
        ):
            await DingTalkWikiSpaceService._list_nodes_in_wikispace(
                user_id=9,
                workspace_id="WS1",
                all_nodes=all_nodes,
            )

        assert all_nodes == [
            {"nodeType": "folder", "workspaceId": "WS1", "parentId": "WS1"},
            {
                "nodeId": "doc-1",
                "nodeType": "doc",
                "workspaceId": "WS1",
                "parentId": "WS1",
            },
        ]
        mock_recursive.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_handles_empty_first_page_without_recursion(self) -> None:
        """Empty result stops pagination and avoids recursive folder traversal."""
        all_nodes: list[dict] = []

        with (
            patch(
                "app.services.dingtalk_wikispace_service.dingtalk_dws_service.list_nodes",
                new=AsyncMock(return_value=([], None)),
            ),
            patch.object(
                DingTalkDocService,
                "_list_nodes_recursive",
                new=AsyncMock(),
            ) as mock_recursive,
        ):
            await DingTalkWikiSpaceService._list_nodes_in_wikispace(
                user_id=9,
                workspace_id="WS1",
                all_nodes=all_nodes,
            )

        assert all_nodes == []
        mock_recursive.assert_not_awaited()


class TestFetchAllWikispaceNodes:
    """Tests for DingTalkWikiSpaceService._fetch_all_wikispace_nodes."""

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_kbs(self) -> None:
        """Returns empty list when DWS returns no knowledge bases."""
        with patch.object(
            DingTalkWikiSpaceService,
            "_list_wiki_spaces",
            new=AsyncMock(return_value=[]),
        ):
            result = await DingTalkWikiSpaceService._fetch_all_wikispace_nodes(
                user_id=9
            )

        assert result == []

    @pytest.mark.asyncio
    async def test_adds_kb_root_as_folder_node(self) -> None:
        """Each knowledge base is added as a folder-type root node."""
        kb_nodes = [{"workspaceId": "WSABC", "name": "Test KB"}]

        with (
            patch.object(
                DingTalkWikiSpaceService,
                "_list_wiki_spaces",
                new=AsyncMock(return_value=kb_nodes),
            ),
            patch.object(
                DingTalkWikiSpaceService,
                "_list_nodes_in_wikispace",
                new=AsyncMock(return_value=None),
            ) as mock_list_nodes,
        ):
            result = await DingTalkWikiSpaceService._fetch_all_wikispace_nodes(
                user_id=9
            )

        assert len(result) == 1
        kb_root = result[0]
        assert kb_root["nodeId"] == "WSABC"
        assert kb_root["nodeType"] == "folder"
        assert kb_root["workspaceId"] == "WSABC"
        assert kb_root["name"] == "Test KB"
        mock_list_nodes.assert_awaited_once()
        assert mock_list_nodes.await_args.kwargs["user_id"] == 9
        assert mock_list_nodes.await_args.kwargs["workspace_id"] == "WSABC"

    @pytest.mark.asyncio
    async def test_skips_kb_with_no_workspace_id(self) -> None:
        """Skips KB nodes that have no workspaceId/nodeId/id field."""
        kb_nodes = [
            {"name": "No ID KB"},
            {"workspaceId": "WS2", "name": "Good KB"},
        ]
        list_nodes_calls: list[str] = []

        async def track_call(
            user_id: int,
            workspace_id: str,
            all_nodes: list,
        ) -> None:
            list_nodes_calls.append(workspace_id)

        with (
            patch.object(
                DingTalkWikiSpaceService,
                "_list_wiki_spaces",
                new=AsyncMock(return_value=kb_nodes),
            ),
            patch.object(
                DingTalkWikiSpaceService,
                "_list_nodes_in_wikispace",
                new=AsyncMock(side_effect=track_call),
            ),
        ):
            result = await DingTalkWikiSpaceService._fetch_all_wikispace_nodes(
                user_id=9
            )

        assert list_nodes_calls == ["WS2"]
        assert len(result) == 1
        assert result[0]["workspaceId"] == "WS2"

    @pytest.mark.asyncio
    async def test_continues_after_kb_error(self) -> None:
        """Continues syncing remaining KBs even if one fails."""
        kb_nodes = [
            {"workspaceId": "WS_FAIL", "name": "Failing KB"},
            {"workspaceId": "WS_OK", "name": "Good KB"},
        ]
        call_count = 0

        async def maybe_fail(
            user_id: int,
            workspace_id: str,
            all_nodes: list,
        ) -> None:
            nonlocal call_count
            call_count += 1
            if workspace_id == "WS_FAIL":
                raise ConnectionError("DWS connection failed")

        with (
            patch.object(
                DingTalkWikiSpaceService,
                "_list_wiki_spaces",
                new=AsyncMock(return_value=kb_nodes),
            ),
            patch.object(
                DingTalkWikiSpaceService,
                "_list_nodes_in_wikispace",
                new=AsyncMock(side_effect=maybe_fail),
            ),
        ):
            result = await DingTalkWikiSpaceService._fetch_all_wikispace_nodes(
                user_id=9
            )

        assert call_count == 2
        assert len(result) == 1
        assert result[0]["workspaceId"] == "WS_OK"
        assert all(node.get("workspaceId") != "WS_FAIL" for node in result)


class TestDedupeNodesById:
    """Tests for DingTalkWikiSpaceService._dedupe_nodes_by_id."""

    def test_keeps_more_complete_duplicate(self) -> None:
        """More complete node replaces less complete node with same ID."""
        nodes = [
            {"nodeId": "dup-1", "name": "Node", "url": ""},
            {
                "nodeId": "dup-1",
                "name": "Node",
                "url": "https://example.com",
                "workspaceId": "WS1",
            },
        ]

        result = DingTalkWikiSpaceService._dedupe_nodes_by_id(nodes)

        assert len(result) == 1
        assert result[0]["url"] == "https://example.com"
        assert result[0]["workspaceId"] == "WS1"

    def test_keeps_first_node_when_completeness_ties(self) -> None:
        """When completeness is tied, the first node remains selected."""
        first = {"nodeId": "dup-2", "name": "First", "workspaceId": "WS1"}
        second = {"nodeId": "dup-2", "name": "Second", "workspaceId": "WS2"}

        result = DingTalkWikiSpaceService._dedupe_nodes_by_id([first, second])

        assert len(result) == 1
        assert result[0]["name"] == "First"
        assert result[0]["workspaceId"] == "WS1"


class TestReadHelpers:
    """Tests for read-only wikispace helper methods."""

    def test_get_wikispace_nodes_returns_only_active_wikispace_nodes(
        self, test_db, test_user
    ) -> None:
        """Only active nodes from wikispace source are returned."""
        now = datetime.now()
        active_wikispace = DingtalkSyncedNode(
            user_id=test_user.id,
            dingtalk_node_id="w" * 32,
            name="Active Wiki",
            doc_url="https://alidocs.dingtalk.com/i/nodes/wiki",
            parent_node_id="",
            node_type="folder",
            source=DingTalkNodeSource.WIKISPACE.value,
            is_active=True,
            last_synced_at=now,
            content_updated_at=now,
        )
        inactive_wikispace = DingtalkSyncedNode(
            user_id=test_user.id,
            dingtalk_node_id="x" * 32,
            name="Inactive Wiki",
            doc_url="https://alidocs.dingtalk.com/i/nodes/wiki2",
            parent_node_id="",
            node_type="folder",
            source=DingTalkNodeSource.WIKISPACE.value,
            is_active=False,
            last_synced_at=now,
            content_updated_at=now,
        )
        docs_node = DingtalkSyncedNode(
            user_id=test_user.id,
            dingtalk_node_id="d" * 32,
            name="Docs Node",
            doc_url="https://alidocs.dingtalk.com/i/nodes/doc",
            parent_node_id="",
            node_type="doc",
            source=DingTalkNodeSource.DOCS.value,
            is_active=True,
            last_synced_at=now,
            content_updated_at=now,
        )
        test_db.add_all([active_wikispace, inactive_wikispace, docs_node])
        test_db.commit()

        result = DingTalkWikiSpaceService.get_wikispace_nodes(test_user.id, test_db)

        assert len(result) == 1
        assert result[0].name == "Active Wiki"

    @patch.object(DingTalkWikiSpaceService, "is_configured", return_value=True)
    def test_get_sync_status_counts_only_active_wikispace_nodes(
        self, _mock_is_configured: MagicMock, test_db, test_user
    ) -> None:
        """Sync status excludes docs source and inactive wikispace rows."""
        older = datetime(2026, 1, 1, 10, 0, 0)
        newer = datetime(2026, 1, 2, 10, 0, 0)
        active_old = DingtalkSyncedNode(
            user_id=test_user.id,
            dingtalk_node_id="a" * 32,
            name="Active Old",
            doc_url="https://alidocs.dingtalk.com/i/nodes/a",
            parent_node_id="",
            node_type="folder",
            source=DingTalkNodeSource.WIKISPACE.value,
            is_active=True,
            last_synced_at=older,
            content_updated_at=older,
        )
        active_new = DingtalkSyncedNode(
            user_id=test_user.id,
            dingtalk_node_id="b" * 32,
            name="Active New",
            doc_url="https://alidocs.dingtalk.com/i/nodes/b",
            parent_node_id="",
            node_type="doc",
            source=DingTalkNodeSource.WIKISPACE.value,
            is_active=True,
            last_synced_at=newer,
            content_updated_at=newer,
        )
        docs_node = DingtalkSyncedNode(
            user_id=test_user.id,
            dingtalk_node_id="c" * 32,
            name="Docs Node",
            doc_url="https://alidocs.dingtalk.com/i/nodes/c",
            parent_node_id="",
            node_type="doc",
            source=DingTalkNodeSource.DOCS.value,
            is_active=True,
            last_synced_at=datetime(2026, 1, 3, 10, 0, 0),
            content_updated_at=datetime(2026, 1, 3, 10, 0, 0),
        )
        test_db.add_all([active_old, active_new, docs_node])
        test_db.commit()

        status = DingTalkWikiSpaceService.get_sync_status(test_user, test_db)

        assert status["is_configured"] is True
        assert status["total_nodes"] == 2
        assert status["last_synced_at"] == newer


class TestSyncWikispaceNodes:
    """Tests for DingTalkWikiSpaceService.sync_wikispace_nodes."""

    @pytest.mark.asyncio
    async def test_raises_when_not_authorized(self) -> None:
        """Raises ValueError when DingTalk DWS is not authorized."""
        mock_user = MagicMock(id=9)

        with patch(
            "app.services.dingtalk_wikispace_service.dingtalk_dws_service.auth_status",
            new=AsyncMock(return_value={"is_authenticated": False}),
        ):
            with pytest.raises(ValueError, match="not authorized"):
                await DingTalkWikiSpaceService.sync_wikispace_nodes(
                    mock_user,
                    MagicMock(),
                )

    @pytest.mark.asyncio
    async def test_fetches_org_spaces_and_syncs_wikispace_source(self) -> None:
        """Authorized sync persists org knowledge-base nodes as wikispace rows."""
        mock_user = MagicMock(id=9)
        mock_db = MagicMock()
        nodes = [{"nodeId": "doc-1", "nodeType": "doc", "workspaceId": "WS1"}]

        with (
            patch(
                "app.services.dingtalk_wikispace_service.dingtalk_dws_service.auth_status",
                new=AsyncMock(return_value={"is_authenticated": True}),
            ) as mock_auth,
            patch.object(
                DingTalkWikiSpaceService,
                "_fetch_all_wikispace_nodes",
                new=AsyncMock(return_value=nodes),
            ) as mock_fetch,
            patch.object(
                DingTalkDocService,
                "_sync_nodes_to_db",
                return_value={
                    "added": 1,
                    "updated": 0,
                    "deleted": 0,
                    "total": 1,
                    "sync_time": datetime.now(),
                },
            ) as mock_sync,
        ):
            result = await DingTalkWikiSpaceService.sync_wikispace_nodes(
                mock_user,
                mock_db,
            )

        mock_auth.assert_awaited_once_with(mock_user.id)
        mock_fetch.assert_awaited_once_with(mock_user.id)
        mock_sync.assert_called_once()
        assert mock_sync.call_args.kwargs["source"] == DingTalkNodeSource.WIKISPACE
        assert result["added"] == 1
        assert result["mcp_nodes_fetched"] == 1
