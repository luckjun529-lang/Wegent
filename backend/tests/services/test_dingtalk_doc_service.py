# SPDX-FileCopyrightText: 2025 Weibo, Inc.
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for DingTalk document sync service."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.orm import Session

from app.models.dingtalk_doc import DingtalkSyncedNode
from app.models.user import User
from app.services.dingtalk_doc_service import DingTalkDocService


class TestLegacyMcpUrlAccessor:
    """Tests for legacy MCP compatibility helpers."""

    def test_get_user_dingtalk_mcp_url_returns_none(self) -> None:
        """MCP URLs are no longer used by the DingTalk docs sync path."""
        assert DingTalkDocService.get_user_dingtalk_mcp_url(MagicMock()) is None


class TestIsConfigured:
    """Tests for is_configured."""

    def test_returns_true_because_dws_is_backend_managed(self) -> None:
        """Authorization is checked by DWS auth status at sync/read time."""
        assert DingTalkDocService.is_configured(MagicMock()) is True


class TestSyncDingtalkDocs:
    """Tests for sync_dingtalk_docs."""

    @pytest.mark.asyncio
    async def test_raises_when_dws_is_not_authorized(self) -> None:
        """Sync asks the user to authorize DingTalk before reading documents."""
        user = MagicMock(id=123)

        with patch(
            "app.services.dingtalk_doc_service.dingtalk_dws_service.auth_status",
            new=AsyncMock(
                return_value={
                    "is_authenticated": False,
                    "auth_status": "unauthenticated",
                }
            ),
        ):
            with pytest.raises(ValueError, match="not authorized"):
                await DingTalkDocService.sync_dingtalk_docs(user, MagicMock())

    @pytest.mark.asyncio
    async def test_syncs_my_wiki_space_nodes(self) -> None:
        """Sync reads personal DingTalk spaces and persists fetched nodes."""
        user = MagicMock(id=123)
        db = MagicMock()
        fetched_nodes = [{"nodeId": "doc-1", "nodeType": "doc"}]

        with (
            patch(
                "app.services.dingtalk_doc_service.dingtalk_dws_service.auth_status",
                new=AsyncMock(return_value={"is_authenticated": True}),
            ) as mock_auth,
            patch.object(
                DingTalkDocService,
                "_fetch_all_nodes",
                new=AsyncMock(return_value=fetched_nodes),
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
            result = await DingTalkDocService.sync_dingtalk_docs(user, db)

        mock_auth.assert_awaited_once_with(user.id)
        mock_fetch.assert_awaited_once_with(user.id)
        mock_sync.assert_called_once()
        assert result["added"] == 1
        assert result["mcp_nodes_fetched"] == 1

    @pytest.mark.asyncio
    async def test_fetch_all_nodes_lists_my_wiki_spaces(self) -> None:
        """Personal documents are fetched from DWS myWikiSpace spaces."""
        with (
            patch(
                "app.services.dingtalk_doc_service.dingtalk_dws_service.list_spaces",
                new=AsyncMock(return_value=[{"workspaceId": "WS1"}]),
            ) as mock_spaces,
            patch.object(
                DingTalkDocService,
                "_list_nodes_recursive",
                new=AsyncMock(),
            ) as mock_recursive,
        ):
            result = await DingTalkDocService._fetch_all_nodes(user_id=7)

        assert result == []
        mock_spaces.assert_awaited_once_with(7, "myWikiSpace")
        mock_recursive.assert_awaited_once_with(
            user_id=7,
            workspace_id="WS1",
            folder_id=None,
            all_nodes=[],
            depth=0,
        )


class TestSyncNodesToDb:
    """Tests for _sync_nodes_to_db using real database session."""

    def test_adds_new_nodes(self, test_db: Session, test_user: User) -> None:
        """New nodes are added to the database."""
        now = datetime.now(timezone.utc)
        nodes = [
            {
                "nodeId": "abc123abc123abc123abc123abc12301",
                "name": "Test Doc",
                "nodeType": "doc",
                "url": "https://alidocs.dingtalk.com/i/nodes/abc123abc123abc123abc123abc12301",
                "parentId": None,
                "workspaceId": "ws001",
                "contentType": "ALIDOC",
                "extension": "adoc",
            },
            {
                "nodeId": "abc123abc123abc123abc123abc12302",
                "name": "Test Folder",
                "nodeType": "folder",
                "url": "https://alidocs.dingtalk.com/i/nodes/abc123abc123abc123abc123abc12302",
                "parentId": None,
                "workspaceId": "ws001",
                "contentType": None,
                "extension": None,
            },
        ]

        result = DingTalkDocService._sync_nodes_to_db(test_user.id, nodes, now, test_db)

        assert result["added"] == 2
        assert result["updated"] == 0
        assert result["deleted"] == 0
        assert result["total"] == 2

        # Verify records in database
        db_nodes = (
            test_db.query(DingtalkSyncedNode)
            .filter(DingtalkSyncedNode.user_id == test_user.id)
            .all()
        )
        assert len(db_nodes) == 2

    def test_updates_existing_nodes(self, test_db: Session, test_user: User) -> None:
        """Existing nodes are updated when data changes."""
        now = datetime.now(timezone.utc)
        dingtalk_node_id = "abc123abc123abc123abc123abc12303"

        # Create an existing node
        existing = DingtalkSyncedNode(
            user_id=test_user.id,
            dingtalk_node_id=dingtalk_node_id,
            name="Old Name",
            doc_url=f"https://alidocs.dingtalk.com/i/nodes/{dingtalk_node_id}",
            parent_node_id="",
            node_type="doc",
            workspace_id="ws001",
            content_type="ALIDOC",
            is_active=True,
            last_synced_at=now,
        )
        test_db.add(existing)
        test_db.commit()

        # Sync with updated name
        new_now = datetime.now(timezone.utc)
        nodes = [
            {
                "nodeId": dingtalk_node_id,
                "name": "New Name",
                "nodeType": "doc",
                "url": f"https://alidocs.dingtalk.com/i/nodes/{dingtalk_node_id}",
                "parentId": None,
                "workspaceId": "ws001",
                "contentType": "ALIDOC",
                "extension": "adoc",
            },
        ]

        result = DingTalkDocService._sync_nodes_to_db(
            test_user.id, nodes, new_now, test_db
        )

        assert result["added"] == 0
        assert result["updated"] == 1
        assert result["deleted"] == 0
        assert result["total"] == 1

        # Verify name was updated
        test_db.refresh(existing)
        assert existing.name == "New Name"

    def test_marks_missing_nodes_as_inactive(
        self, test_db: Session, test_user: User
    ) -> None:
        """Nodes not in the sync list are marked as inactive."""
        # Use local time (no timezone) to match how _parse_update_time works
        now = datetime.now()

        # Create existing nodes with empty strings for parent_node_id
        # to match how sync processes nodes without parentId
        node1 = DingtalkSyncedNode(
            user_id=test_user.id,
            dingtalk_node_id="abc123abc123abc123abc123abc12304",
            name="Keep This",
            doc_url="https://alidocs.dingtalk.com/i/nodes/abc123abc123abc123abc123abc12304",
            parent_node_id="",
            node_type="doc",
            workspace_id="",
            content_type="",
            content_updated_at=now,
            is_active=True,
            last_synced_at=now,
        )
        node2 = DingtalkSyncedNode(
            user_id=test_user.id,
            dingtalk_node_id="abc123abc123abc123abc123abc12305",
            name="Remove This",
            doc_url="https://alidocs.dingtalk.com/i/nodes/abc123abc123abc123abc123abc12305",
            parent_node_id="",
            node_type="doc",
            workspace_id="",
            content_type="",
            content_updated_at=now,
            is_active=True,
            last_synced_at=now,
        )
        test_db.add_all([node1, node2])
        test_db.commit()

        # Sync with only node1 present - use updateTime to match content_updated_at
        new_now = datetime.now()
        nodes = [
            {
                "nodeId": "abc123abc123abc123abc123abc12304",
                "name": "Keep This",
                "nodeType": "doc",
                "updateTime": now.timestamp(),  # Match existing content_updated_at
            },
        ]

        result = DingTalkDocService._sync_nodes_to_db(
            test_user.id, nodes, new_now, test_db
        )

        assert result["added"] == 0
        assert result["updated"] == 0
        assert result["deleted"] == 1
        assert result["total"] == 1

        # Verify node2 is now inactive
        test_db.refresh(node2)
        assert node2.is_active is False

    def test_reactivates_inactive_node(self, test_db: Session, test_user: User) -> None:
        """Previously inactive nodes are reactivated when they reappear in sync."""
        now = datetime.now(timezone.utc)
        dingtalk_node_id = "abc123abc123abc123abc123abc12306"

        # Create an inactive node
        existing = DingtalkSyncedNode(
            user_id=test_user.id,
            dingtalk_node_id=dingtalk_node_id,
            name="Reactivated Doc",
            doc_url=f"https://alidocs.dingtalk.com/i/nodes/{dingtalk_node_id}",
            parent_node_id="",
            node_type="doc",
            is_active=False,
            last_synced_at=now,
        )
        test_db.add(existing)
        test_db.commit()

        # Sync with the node reappearing
        new_now = datetime.now(timezone.utc)
        nodes = [
            {
                "nodeId": dingtalk_node_id,
                "name": "Reactivated Doc",
                "nodeType": "doc",
            },
        ]

        result = DingTalkDocService._sync_nodes_to_db(
            test_user.id, nodes, new_now, test_db
        )

        # Should be counted as updated because is_active changed
        assert result["updated"] == 1
        assert result["total"] == 1

        test_db.refresh(existing)
        assert existing.is_active is True

    def test_skips_nodes_without_node_id(
        self, test_db: Session, test_user: User
    ) -> None:
        """Nodes with missing nodeId are skipped."""
        now = datetime.now(timezone.utc)
        nodes = [
            {"name": "No Node ID", "nodeType": "doc"},
            {"nodeId": "", "name": "Empty Node ID", "nodeType": "doc"},
        ]

        result = DingTalkDocService._sync_nodes_to_db(test_user.id, nodes, now, test_db)

        assert result["added"] == 0
        assert result["total"] == 0

    def test_builds_default_url_when_missing(
        self, test_db: Session, test_user: User
    ) -> None:
        """Default doc URL is built when url field is missing."""
        now = datetime.now(timezone.utc)
        dingtalk_node_id = "abc123abc123abc123abc123abc12307"

        nodes = [
            {
                "nodeId": dingtalk_node_id,
                "name": "No URL Doc",
                "nodeType": "doc",
            },
        ]

        DingTalkDocService._sync_nodes_to_db(test_user.id, nodes, now, test_db)

        db_node = (
            test_db.query(DingtalkSyncedNode)
            .filter(DingtalkSyncedNode.dingtalk_node_id == dingtalk_node_id)
            .first()
        )
        assert db_node is not None
        assert (
            db_node.doc_url
            == f"https://alidocs.dingtalk.com/i/nodes/{dingtalk_node_id}"
        )

    def test_maps_node_types_correctly(self, test_db: Session, test_user: User) -> None:
        """Node types are mapped correctly from DingTalk data."""
        now = datetime.now(timezone.utc)
        nodes = [
            {"nodeId": "a" * 32, "name": "Folder", "nodeType": "folder"},
            {"nodeId": "b" * 32, "name": "File", "nodeType": "file"},
            {"nodeId": "c" * 32, "name": "Doc", "nodeType": "doc"},
            {"nodeId": "d" * 32, "name": "Other", "nodeType": "other"},
        ]

        DingTalkDocService._sync_nodes_to_db(test_user.id, nodes, now, test_db)

        folder = (
            test_db.query(DingtalkSyncedNode)
            .filter(DingtalkSyncedNode.dingtalk_node_id == "a" * 32)
            .first()
        )
        assert folder.node_type == "folder"

        file = (
            test_db.query(DingtalkSyncedNode)
            .filter(DingtalkSyncedNode.dingtalk_node_id == "b" * 32)
            .first()
        )
        assert file.node_type == "file"

        doc = (
            test_db.query(DingtalkSyncedNode)
            .filter(DingtalkSyncedNode.dingtalk_node_id == "c" * 32)
            .first()
        )
        assert doc.node_type == "doc"

        other = (
            test_db.query(DingtalkSyncedNode)
            .filter(DingtalkSyncedNode.dingtalk_node_id == "d" * 32)
            .first()
        )
        # Unknown types default to "doc"
        assert other.node_type == "doc"

    def test_no_change_counts_as_neither_added_nor_updated(
        self, test_db: Session, test_user: User
    ) -> None:
        """Existing node with no field changes is counted as neither added nor updated."""
        # Use local time (no timezone) to match how _parse_update_time works
        now = datetime.now()
        dingtalk_node_id = "abc123abc123abc123abc123abc12308"

        existing = DingtalkSyncedNode(
            user_id=test_user.id,
            dingtalk_node_id=dingtalk_node_id,
            name="Stable Doc",
            doc_url=f"https://alidocs.dingtalk.com/i/nodes/{dingtalk_node_id}",
            parent_node_id="",
            node_type="doc",
            workspace_id="",
            content_type="",
            content_updated_at=now,
            is_active=True,
            last_synced_at=now,
        )
        test_db.add(existing)
        test_db.commit()

        # Sync same data with matching content_updated_at (via updateTime)
        new_now = datetime.now()
        nodes = [
            {
                "nodeId": dingtalk_node_id,
                "name": "Stable Doc",
                "nodeType": "doc",
                "updateTime": now.timestamp(),  # Match existing content_updated_at
            },
        ]

        result = DingTalkDocService._sync_nodes_to_db(
            test_user.id, nodes, new_now, test_db
        )

        assert result["added"] == 0
        assert result["updated"] == 0
        assert result["total"] == 1

        # last_synced_at should still be updated
        test_db.refresh(existing)
        assert existing.last_synced_at == new_now


class TestGetDingtalkDocs:
    """Tests for get_dingtalk_docs."""

    def test_returns_only_active_nodes(self, test_db: Session, test_user: User) -> None:
        """Only active nodes are returned."""
        now = datetime.now(timezone.utc)

        active = DingtalkSyncedNode(
            user_id=test_user.id,
            dingtalk_node_id="a" * 32,
            name="Active Doc",
            doc_url="https://alidocs.dingtalk.com/i/nodes/aaa",
            parent_node_id="",
            node_type="doc",
            is_active=True,
            last_synced_at=now,
        )
        inactive = DingtalkSyncedNode(
            user_id=test_user.id,
            dingtalk_node_id="b" * 32,
            name="Inactive Doc",
            doc_url="https://alidocs.dingtalk.com/i/nodes/bbb",
            parent_node_id="",
            node_type="doc",
            is_active=False,
            last_synced_at=now,
        )
        test_db.add_all([active, inactive])
        test_db.commit()

        result = DingTalkDocService.get_dingtalk_docs(test_user.id, test_db)

        assert len(result) == 1
        assert result[0].name == "Active Doc"

    def test_returns_empty_list_when_no_nodes(
        self, test_db: Session, test_user: User
    ) -> None:
        """Returns empty list when user has no synced nodes."""
        result = DingTalkDocService.get_dingtalk_docs(test_user.id, test_db)

        assert result == []

    def test_orders_by_node_type_and_name(
        self, test_db: Session, test_user: User
    ) -> None:
        """Results are ordered by node_type then name (alphabetical)."""
        now = datetime.now(timezone.utc)

        doc_node = DingtalkSyncedNode(
            user_id=test_user.id,
            dingtalk_node_id="a" * 32,
            name="Zebra Doc",
            doc_url="https://alidocs.dingtalk.com/i/nodes/aaa",
            parent_node_id="",
            node_type="doc",
            is_active=True,
            last_synced_at=now,
        )
        folder_node = DingtalkSyncedNode(
            user_id=test_user.id,
            dingtalk_node_id="b" * 32,
            name="Alpha Folder",
            doc_url="https://alidocs.dingtalk.com/i/nodes/bbb",
            parent_node_id="",
            node_type="folder",
            is_active=True,
            last_synced_at=now,
        )
        test_db.add_all([doc_node, folder_node])
        test_db.commit()

        result = DingTalkDocService.get_dingtalk_docs(test_user.id, test_db)

        # Alphabetical sort: "doc" < "folder"
        assert result[0].node_type == "doc"
        assert result[0].name == "Zebra Doc"
        assert result[1].node_type == "folder"
        assert result[1].name == "Alpha Folder"

    def test_filters_by_user_id(self, test_db: Session, test_user: User) -> None:
        """Only nodes belonging to the specified user are returned."""
        now = datetime.now(timezone.utc)

        # Create node for test_user
        node = DingtalkSyncedNode(
            user_id=test_user.id,
            dingtalk_node_id="a" * 32,
            name="User Doc",
            doc_url="https://alidocs.dingtalk.com/i/nodes/aaa",
            parent_node_id="",
            node_type="doc",
            is_active=True,
            last_synced_at=now,
        )
        test_db.add(node)
        test_db.commit()

        # Query with different user_id
        other_user_id = test_user.id + 9999
        result = DingTalkDocService.get_dingtalk_docs(other_user_id, test_db)

        assert result == []


class TestGetSyncStatus:
    """Tests for get_sync_status."""

    @patch.object(DingTalkDocService, "is_configured", return_value=True)
    def test_returns_status_when_configured(
        self, mock_is_configured: MagicMock, test_db: Session, test_user: User
    ) -> None:
        """Returns correct status when DingTalk is configured with synced nodes."""
        now = datetime.now(timezone.utc)
        node = DingtalkSyncedNode(
            user_id=test_user.id,
            dingtalk_node_id="a" * 32,
            name="Synced Doc",
            doc_url="https://alidocs.dingtalk.com/i/nodes/aaa",
            parent_node_id="",
            node_type="doc",
            is_active=True,
            last_synced_at=now,
        )
        test_db.add(node)
        test_db.commit()

        status = DingTalkDocService.get_sync_status(test_user, test_db)

        assert status["is_configured"] is True
        assert status["total_nodes"] == 1
        assert status["last_synced_at"] is not None

    @patch.object(DingTalkDocService, "is_configured", return_value=False)
    def test_returns_not_configured_when_no_mcp(
        self, mock_is_configured: MagicMock, test_db: Session, test_user: User
    ) -> None:
        """Returns is_configured=False when MCP URL is not set."""
        status = DingTalkDocService.get_sync_status(test_user, test_db)

        assert status["is_configured"] is False
        assert status["total_nodes"] == 0
        assert status["last_synced_at"] is None

    @patch.object(DingTalkDocService, "is_configured", return_value=True)
    def test_returns_zero_nodes_when_no_syncs(
        self, mock_is_configured: MagicMock, test_db: Session, test_user: User
    ) -> None:
        """Returns total_nodes=0 when user has configured but never synced."""
        status = DingTalkDocService.get_sync_status(test_user, test_db)

        assert status["is_configured"] is True
        assert status["total_nodes"] == 0
        assert status["last_synced_at"] is None

    @patch.object(DingTalkDocService, "is_configured", return_value=True)
    def test_excludes_inactive_nodes_from_count(
        self, mock_is_configured: MagicMock, test_db: Session, test_user: User
    ) -> None:
        """Inactive nodes are not counted in total_nodes."""
        now = datetime.now(timezone.utc)
        active = DingtalkSyncedNode(
            user_id=test_user.id,
            dingtalk_node_id="a" * 32,
            name="Active",
            doc_url="https://alidocs.dingtalk.com/i/nodes/aaa",
            parent_node_id="",
            node_type="doc",
            is_active=True,
            last_synced_at=now,
        )
        inactive = DingtalkSyncedNode(
            user_id=test_user.id,
            dingtalk_node_id="b" * 32,
            name="Inactive",
            doc_url="https://alidocs.dingtalk.com/i/nodes/bbb",
            parent_node_id="",
            node_type="doc",
            is_active=False,
            last_synced_at=now,
        )
        test_db.add_all([active, inactive])
        test_db.commit()

        status = DingTalkDocService.get_sync_status(test_user, test_db)

        assert status["total_nodes"] == 1


class TestListNodesRecursive:
    """Tests for _list_nodes_recursive - verifies folder traversal behavior."""

    @pytest.mark.asyncio
    async def test_recurses_into_folder_without_has_children_flag(self) -> None:
        """Folders are recursed into even when hasChildren is absent."""
        root_folder_node = {
            "nodeId": "folder001",
            "name": "Root Folder",
            "nodeType": "folder",
        }
        call_log: list[str | None] = []

        async def list_nodes(
            user_id: int,
            *,
            workspace_id: str,
            folder_id: str | None = None,
            cursor: str | None = None,
        ) -> tuple[list[dict], str | None]:
            call_log.append(folder_id)
            if folder_id is None:
                return [root_folder_node], None
            return [], None

        all_nodes: list = []
        with patch(
            "app.services.dingtalk_doc_service.dingtalk_dws_service.list_nodes",
            new=AsyncMock(side_effect=list_nodes),
        ):
            await DingTalkDocService._list_nodes_recursive(
                user_id=1,
                workspace_id="WS1",
                folder_id=None,
                all_nodes=all_nodes,
                depth=0,
            )

        assert call_log == [None, "folder001"]
        assert len(all_nodes) == 1
        assert all_nodes[0]["nodeId"] == "folder001"
        assert all_nodes[0]["workspaceId"] == "WS1"

    @pytest.mark.asyncio
    async def test_recurses_into_folder_with_has_children_false(self) -> None:
        """Folders are recursed into even when hasChildren is explicitly False."""
        folder_node = {
            "nodeId": "folder002",
            "name": "Folder With False HasChildren",
            "nodeType": "folder",
            "hasChildren": False,
        }
        call_log: list[str | None] = []

        async def list_nodes(
            user_id: int,
            *,
            workspace_id: str,
            folder_id: str | None = None,
            cursor: str | None = None,
        ) -> tuple[list[dict], str | None]:
            call_log.append(folder_id)
            if folder_id is None:
                return [folder_node], None
            return [], None

        all_nodes: list = []
        with patch(
            "app.services.dingtalk_doc_service.dingtalk_dws_service.list_nodes",
            new=AsyncMock(side_effect=list_nodes),
        ):
            await DingTalkDocService._list_nodes_recursive(
                user_id=1,
                workspace_id="WS1",
                folder_id=None,
                all_nodes=all_nodes,
                depth=0,
            )

        assert call_log == [None, "folder002"]

    @pytest.mark.asyncio
    async def test_injects_parent_id_into_child_nodes(self) -> None:
        """Child nodes get parentId injected from the calling folder_id.

        The DWS wiki node list API may not return parent node information.
        When we call list_nodes(folderId=X), the returned nodes are children of X,
        so we must inject parentId=X into each returned node to preserve the
        tree structure in the database.
        """
        folder_id = "folder_parent_001"
        child_doc = {
            "nodeId": "doc_child_001",
            "name": "Child Document",
            "nodeType": "doc",
        }

        all_nodes: list = []
        with patch(
            "app.services.dingtalk_doc_service.dingtalk_dws_service.list_nodes",
            new=AsyncMock(return_value=([child_doc], None)),
        ):
            await DingTalkDocService._list_nodes_recursive(
                user_id=1,
                workspace_id="WS1",
                folder_id=folder_id,
                all_nodes=all_nodes,
                depth=0,
            )

        assert len(all_nodes) == 1
        assert all_nodes[0]["nodeId"] == "doc_child_001"
        assert all_nodes[0]["workspaceId"] == "WS1"
        assert all_nodes[0]["parentId"] == folder_id, (
            "parentId should be injected from the folder_id parameter "
            "since DingTalk DWS may not return parent info"
        )

    @pytest.mark.asyncio
    async def test_root_nodes_have_no_parent_id_injected(self) -> None:
        """Root-level nodes (folder_id=None) do not get a parentId injected."""
        root_doc = {
            "nodeId": "doc_root_001",
            "name": "Root Document",
            "nodeType": "doc",
        }

        all_nodes: list = []
        with patch(
            "app.services.dingtalk_doc_service.dingtalk_dws_service.list_nodes",
            new=AsyncMock(return_value=([root_doc], None)),
        ):
            await DingTalkDocService._list_nodes_recursive(
                user_id=1,
                workspace_id="WS1",
                folder_id=None,
                all_nodes=all_nodes,
                depth=0,
            )

        assert len(all_nodes) == 1
        assert all_nodes[0].get("parentId") is None

    @pytest.mark.asyncio
    async def test_continues_root_pagination(self) -> None:
        """Root pagination continues until DWS stops returning a cursor."""
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
                return [{"nodeId": "doc-1", "nodeType": "doc"}], "next"
            return [{"nodeId": "doc-2", "nodeType": "doc"}], None

        all_nodes: list = []
        with patch(
            "app.services.dingtalk_doc_service.dingtalk_dws_service.list_nodes",
            new=AsyncMock(side_effect=list_nodes),
        ):
            await DingTalkDocService._list_nodes_recursive(
                user_id=1,
                workspace_id="WS1",
                folder_id=None,
                all_nodes=all_nodes,
                depth=0,
            )

        assert calls == [None, "next"]
        assert [node["nodeId"] for node in all_nodes] == ["doc-1", "doc-2"]


class TestDeleteSyncedNode:
    """Tests for delete_synced_node."""

    def test_deletes_existing_node(self, test_db: Session, test_user: User) -> None:
        """Deletes a node that belongs to the user."""
        now = datetime.now(timezone.utc)
        node = DingtalkSyncedNode(
            user_id=test_user.id,
            dingtalk_node_id="a" * 32,
            name="To Delete",
            doc_url="https://alidocs.dingtalk.com/i/nodes/aaa",
            parent_node_id="",
            node_type="doc",
            is_active=True,
            last_synced_at=now,
        )
        test_db.add(node)
        test_db.commit()

        result = DingTalkDocService.delete_synced_node(node.id, test_user.id, test_db)

        assert result is True
        # Verify node is gone
        assert (
            test_db.query(DingtalkSyncedNode)
            .filter(DingtalkSyncedNode.id == node.id)
            .first()
            is None
        )

    def test_returns_false_for_nonexistent_node(
        self, test_db: Session, test_user: User
    ) -> None:
        """Returns False when node does not exist."""
        result = DingTalkDocService.delete_synced_node(99999, test_user.id, test_db)

        assert result is False

    def test_returns_false_for_wrong_user(
        self, test_db: Session, test_user: User
    ) -> None:
        """Returns False when node belongs to a different user."""
        now = datetime.now(timezone.utc)
        node = DingtalkSyncedNode(
            user_id=test_user.id,
            dingtalk_node_id="a" * 32,
            name="Other User Node",
            doc_url="https://alidocs.dingtalk.com/i/nodes/aaa",
            parent_node_id="",
            node_type="doc",
            is_active=True,
            last_synced_at=now,
        )
        test_db.add(node)
        test_db.commit()

        # Try to delete with a different user_id
        result = DingTalkDocService.delete_synced_node(
            node.id, test_user.id + 9999, test_db
        )

        assert result is False
        # Verify node still exists
        assert (
            test_db.query(DingtalkSyncedNode)
            .filter(DingtalkSyncedNode.id == node.id)
            .first()
            is not None
        )
