# SPDX-FileCopyrightText: 2026 Weibo, Inc.
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for DingTalk team-file synchronization."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.dingtalk_doc import DingTalkNodeSource
from app.services.dingtalk_doc_service import DingTalkDocService
from app.services.dingtalk_dws_service import DwsCommandError
from app.services.dingtalk_team_files_service import DingTalkTeamFilesService


class TestDingTalkTeamFilesService:
    """Tests for team-space discovery, traversal, and persistence routing."""

    @pytest.mark.asyncio
    async def test_lists_org_spaces(self) -> None:
        spaces = [{"spaceId": "1001", "spaceName": "Engineering"}]
        with patch(
            "app.services.dingtalk_team_files_service."
            "dingtalk_dws_service.list_spaces",
            new=AsyncMock(return_value=spaces),
        ) as mock_list:
            result = await DingTalkTeamFilesService._list_team_spaces(9)

        assert result == spaces
        mock_list.assert_awaited_once_with(9, "orgSpace")

    @pytest.mark.asyncio
    async def test_recurses_with_file_id_and_preserves_tree_parent(self) -> None:
        calls: list[tuple[str | None, str | None]] = []

        async def list_nodes(
            user_id: int,
            *,
            space_id: str,
            folder_id: str | None = None,
            cursor: str | None = None,
        ) -> tuple[list[dict], str | None]:
            calls.append((folder_id, cursor))
            if folder_id == "folder-1":
                return ([{"fileId": "child-1", "type": "file", "name": "Child"}], None)
            if cursor is None:
                return (
                    [
                        {"fileId": "folder-1", "dentryType": "1", "name": "Folder"},
                        {"fileId": "file-1", "dentryType": "0", "fileName": "One.pdf"},
                    ],
                    "page-2",
                )
            return ([{"fileId": "file-2", "type": "file", "name": "Two.txt"}], None)

        nodes: list[dict] = []
        with patch(
            "app.services.dingtalk_team_files_service."
            "dingtalk_dws_service.list_drive_nodes",
            new=AsyncMock(side_effect=list_nodes),
        ):
            await DingTalkTeamFilesService._list_nodes_recursive(
                user_id=9,
                space_id="1001",
                parent_node_id="root-1",
                folder_id=None,
                all_nodes=nodes,
                depth=0,
            )

        assert calls == [(None, None), ("folder-1", None), (None, "page-2")]
        by_id = {DingTalkDocService._extract_node_id(node): node for node in nodes}
        assert by_id["folder-1"]["parentId"] == "root-1"
        assert by_id["child-1"]["parentId"] == "folder-1"
        assert by_id["file-2"]["workspaceId"] == "1001"
        assert by_id["folder-1"]["nodeType"] == "folder"

    @pytest.mark.asyncio
    async def test_fetch_adds_team_space_root(self) -> None:
        spaces = [
            {
                "spaceId": "1001",
                "spaceName": "Engineering",
                "rootFolderId": "root-1",
            }
        ]

        async def add_file(**kwargs) -> bool:
            kwargs["all_nodes"].append(
                {
                    "fileId": "file-1",
                    "nodeType": "file",
                    "name": "Roadmap.pdf",
                    "parentId": kwargs["parent_node_id"],
                    "workspaceId": kwargs["space_id"],
                }
            )
            return True

        with (
            patch.object(
                DingTalkTeamFilesService,
                "_list_team_spaces",
                new=AsyncMock(return_value=spaces),
            ),
            patch.object(
                DingTalkTeamFilesService,
                "_list_nodes_recursive",
                new=AsyncMock(side_effect=add_file),
            ) as mock_list,
        ):
            result = await DingTalkTeamFilesService._fetch_all_team_file_nodes(9)

        nodes, traversal_complete = result
        assert traversal_complete is True
        assert [DingTalkDocService._extract_node_id(node) for node in nodes] == [
            "root-1",
            "file-1",
        ]
        assert nodes[0]["name"] == "Engineering"
        assert nodes[0]["nodeType"] == "folder"
        assert nodes[1]["parentId"] == "root-1"
        mock_list.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_fetch_skips_inaccessible_space_and_continues(self) -> None:
        """Stale org-space entries must not block accessible team files."""
        spaces = [
            {"spaceId": "denied", "rootFolderId": "denied-root"},
            {"spaceId": "allowed", "rootFolderId": "allowed-root"},
        ]

        async def list_space(**kwargs) -> bool:
            if kwargs["space_id"] == "denied":
                raise DwsCommandError(
                    "Access denied",
                    server_error_code="forbidden.accessDenied",
                )
            kwargs["all_nodes"].append(
                {
                    "fileId": "file-1",
                    "nodeType": "file",
                    "name": "Roadmap.pdf",
                    "parentId": kwargs["parent_node_id"],
                    "workspaceId": kwargs["space_id"],
                }
            )
            return True

        with (
            patch.object(
                DingTalkTeamFilesService,
                "_list_team_spaces",
                new=AsyncMock(return_value=spaces),
            ),
            patch.object(
                DingTalkTeamFilesService,
                "_list_nodes_recursive",
                new=AsyncMock(side_effect=list_space),
            ),
        ):
            nodes, traversal_complete = (
                await DingTalkTeamFilesService._fetch_all_team_file_nodes(9)
            )

        assert traversal_complete is True
        assert [DingTalkDocService._extract_node_id(node) for node in nodes] == [
            "allowed-root",
            "file-1",
        ]

    @pytest.mark.asyncio
    async def test_fetch_does_not_hide_non_permission_errors(self) -> None:
        """Network and command failures still fail the whole synchronization."""
        spaces = [{"spaceId": "1001", "rootFolderId": "root-1"}]
        with (
            patch.object(
                DingTalkTeamFilesService,
                "_list_team_spaces",
                new=AsyncMock(return_value=spaces),
            ),
            patch.object(
                DingTalkTeamFilesService,
                "_list_nodes_recursive",
                new=AsyncMock(side_effect=DwsCommandError("network failed")),
            ),
        ):
            with pytest.raises(DwsCommandError, match="network failed"):
                await DingTalkTeamFilesService._fetch_all_team_file_nodes(9)

    @pytest.mark.asyncio
    async def test_sync_routes_rows_to_team_files_source(self) -> None:
        user = MagicMock(id=9)
        db = MagicMock()
        nodes = [{"fileId": "file-1", "nodeType": "file", "workspaceId": "1001"}]
        sync_time = datetime.now()

        with (
            patch(
                "app.services.dingtalk_team_files_service."
                "dingtalk_dws_service.auth_status",
                new=AsyncMock(return_value={"is_authenticated": True}),
            ),
            patch.object(
                DingTalkTeamFilesService,
                "_fetch_all_team_file_nodes",
                new=AsyncMock(return_value=(nodes, True)),
            ),
            patch.object(
                DingTalkDocService,
                "_sync_nodes_to_db",
                return_value={
                    "added": 1,
                    "updated": 0,
                    "deleted": 0,
                    "total": 1,
                    "sync_time": sync_time,
                },
            ) as mock_sync,
        ):
            result = await DingTalkTeamFilesService.sync_team_files(user, db)

        assert mock_sync.call_args.kwargs["source"] == DingTalkNodeSource.TEAM_FILES
        assert mock_sync.call_args.kwargs["deactivate_missing"] is True
        assert result["dws_nodes_fetched"] == 1

    @pytest.mark.asyncio
    async def test_stops_repeated_folder_traversal(self) -> None:
        """A malformed folder cycle must not repeatedly call DWS."""
        calls = 0

        async def list_nodes(*args, **kwargs) -> tuple[list[dict], None]:
            nonlocal calls
            calls += 1
            return ([{"fileId": "folder-1", "dentryType": "1", "name": "Folder"}], None)

        nodes: list[dict] = []
        with patch(
            "app.services.dingtalk_team_files_service."
            "dingtalk_dws_service.list_drive_nodes",
            new=AsyncMock(side_effect=list_nodes),
        ):
            complete = await DingTalkTeamFilesService._list_nodes_recursive(
                user_id=9,
                space_id="1001",
                parent_node_id="root-1",
                folder_id="folder-1",
                all_nodes=nodes,
                depth=0,
                visited_folders={("1001", "folder-1")},
            )

        assert complete is True
        assert calls == 0
        assert nodes == []

    @pytest.mark.asyncio
    async def test_does_not_deactivate_cached_nodes_after_incomplete_traversal(
        self,
    ) -> None:
        user = MagicMock(id=9)
        db = MagicMock()
        sync_time = datetime.now()
        with (
            patch(
                "app.services.dingtalk_team_files_service."
                "dingtalk_dws_service.auth_status",
                new=AsyncMock(return_value={"is_authenticated": True}),
            ),
            patch.object(
                DingTalkTeamFilesService,
                "_fetch_all_team_file_nodes",
                new=AsyncMock(return_value=([{"fileId": "file-1"}], False)),
            ),
            patch.object(
                DingTalkDocService,
                "_sync_nodes_to_db",
                return_value={
                    "added": 0,
                    "updated": 0,
                    "deleted": 0,
                    "total": 1,
                    "sync_time": sync_time,
                },
            ) as mock_sync,
        ):
            result = await DingTalkTeamFilesService.sync_team_files(user, db)

        assert mock_sync.call_args.kwargs["deactivate_missing"] is False
        assert result["truncated"] is True

    @pytest.mark.asyncio
    async def test_sync_rejects_unauthorized_user(self) -> None:
        with patch(
            "app.services.dingtalk_team_files_service."
            "dingtalk_dws_service.auth_status",
            new=AsyncMock(return_value={"is_authenticated": False}),
        ):
            with pytest.raises(ValueError, match="not authorized"):
                await DingTalkTeamFilesService.sync_team_files(
                    MagicMock(id=9),
                    MagicMock(),
                )
