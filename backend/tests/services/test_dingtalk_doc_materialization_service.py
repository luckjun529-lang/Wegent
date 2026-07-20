# SPDX-FileCopyrightText: 2026 Weibo, Inc.
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for DingTalk document chat-context materialization."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.orm import Session

from app.models.dingtalk_doc import DingTalkNodeSource, DingtalkSyncedNode
from app.models.user import User
from app.services.dingtalk_doc_materialization_service import (
    DingTalkDocMaterializationService,
)


def _create_synced_node(
    db: Session,
    user_id: int,
    *,
    node_id: str = "dingtalk-node-1",
    name: str = "Project Plan",
    node_type: str = "doc",
    source: str = DingTalkNodeSource.DOCS.value,
    content_type: str = "ALIDOC",
) -> DingtalkSyncedNode:
    now = datetime.now()
    node = DingtalkSyncedNode(
        user_id=user_id,
        dingtalk_node_id=node_id,
        name=name,
        doc_url=f"https://alidocs.dingtalk.com/i/nodes/{node_id}",
        parent_node_id="",
        node_type=node_type,
        workspace_id="WS1",
        source=source,
        content_type=content_type,
        content_updated_at=now,
        is_active=True,
        last_synced_at=now,
    )
    db.add(node)
    db.commit()
    db.refresh(node)
    return node


def _context_payload(
    node: DingtalkSyncedNode,
    *,
    source: str | None = None,
) -> dict:
    return {
        "type": "dingtalk_doc",
        "data": {
            "source": source or node.source,
            "dingtalk_node_id": node.dingtalk_node_id,
            "doc_url": node.doc_url,
            "name": node.name,
            "node_type": node.node_type,
        },
    }


class TestDingTalkDocMaterializationService:
    """Tests for DingTalkDocMaterializationService."""

    @pytest.mark.asyncio
    async def test_materializes_adoc_as_markdown_attachment(
        self,
        test_db: Session,
        test_user: User,
    ) -> None:
        """A selected ALIDOC/adoc node becomes an attachment context."""
        node = _create_synced_node(test_db, test_user.id, name="Project/Plan")
        uploaded_context = MagicMock(id=321, type_data={})

        with (
            patch(
                "app.services.dingtalk_doc_materialization_service."
                "dingtalk_dws_service.doc_info",
                new=AsyncMock(
                    return_value={"contentType": "ALIDOC", "extension": "adoc"}
                ),
            ) as mock_info,
            patch(
                "app.services.dingtalk_doc_materialization_service."
                "dingtalk_dws_service.doc_read",
                new=AsyncMock(return_value={"markdown": "## Body\nHello"}),
            ) as mock_read,
            patch(
                "app.services.dingtalk_doc_materialization_service."
                "context_service.upload_attachment",
                return_value=(uploaded_context, None),
            ) as mock_upload,
            patch.object(test_db, "refresh", return_value=None),
        ):
            attachment_ids = (
                await DingTalkDocMaterializationService.materialize_contexts(
                    db=test_db,
                    user_id=test_user.id,
                    contexts=[_context_payload(node)],
                )
            )

        assert attachment_ids == [321]
        mock_info.assert_awaited_once_with(test_user.id, node.dingtalk_node_id)
        mock_read.assert_awaited_once_with(test_user.id, node.dingtalk_node_id)
        upload_kwargs = mock_upload.call_args.kwargs
        assert upload_kwargs["filename"] == "Project_Plan.md"
        body = upload_kwargs["binary_data"].decode("utf-8")
        assert f"DingTalk node: {node.dingtalk_node_id}" in body
        assert "## Body\nHello" in body
        assert uploaded_context.type_data["source"] == "dingtalk_doc"
        assert uploaded_context.type_data["dingtalk_node_id"] == node.dingtalk_node_id
        assert (
            uploaded_context.type_data["dingtalk_source"]
            == DingTalkNodeSource.DOCS.value
        )

    @pytest.mark.asyncio
    async def test_skips_folder_contexts(
        self,
        test_db: Session,
        test_user: User,
    ) -> None:
        """Folders are ignored because only document/file nodes can be read."""
        folder = _create_synced_node(
            test_db,
            test_user.id,
            node_id="folder-node-1",
            name="Folder",
            node_type="folder",
            content_type="",
        )

        with (
            patch(
                "app.services.dingtalk_doc_materialization_service."
                "dingtalk_dws_service.doc_info",
                new=AsyncMock(),
            ) as mock_info,
            patch(
                "app.services.dingtalk_doc_materialization_service."
                "context_service.upload_attachment",
            ) as mock_upload,
        ):
            attachment_ids = (
                await DingTalkDocMaterializationService.materialize_contexts(
                    db=test_db,
                    user_id=test_user.id,
                    contexts=[_context_payload(folder)],
                )
            )

        assert attachment_ids == []
        mock_info.assert_not_awaited()
        mock_upload.assert_not_called()

    @pytest.mark.asyncio
    async def test_rejects_unsupported_document_types(
        self,
        test_db: Session,
        test_user: User,
    ) -> None:
        """Only ALIDOC/adoc documents are materialized in v1."""
        node = _create_synced_node(
            test_db,
            test_user.id,
            node_id="sheet-node-1",
            name="Sheet",
            content_type="",
        )

        with patch(
            "app.services.dingtalk_doc_materialization_service."
            "dingtalk_dws_service.doc_info",
            new=AsyncMock(
                return_value={"contentType": "AIMTABLE", "extension": "axls"}
            ),
        ):
            with pytest.raises(ValueError, match="not a supported text document"):
                await DingTalkDocMaterializationService.materialize_contexts(
                    db=test_db,
                    user_id=test_user.id,
                    contexts=[_context_payload(node)],
                )

    @pytest.mark.asyncio
    async def test_requires_node_to_belong_to_current_user(
        self,
        test_db: Session,
        test_user: User,
    ) -> None:
        """A DingTalk reference must match a synced node owned by the user."""
        other_node = _create_synced_node(test_db, test_user.id + 999, node_id="other")

        with pytest.raises(ValueError, match="not synced for the current user"):
            await DingTalkDocMaterializationService.materialize_contexts(
                db=test_db,
                user_id=test_user.id,
                contexts=[_context_payload(other_node)],
            )
