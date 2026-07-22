# SPDX-FileCopyrightText: 2026 Weibo, Inc.
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for DingTalk document chat-context materialization."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.orm import Session

from app.models.dingtalk_doc import DingTalkNodeSource, DingtalkSyncedNode
from app.models.user import User
from app.services.dingtalk_doc_materialization_service import (
    BoundedTextBuilder,
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
    async def test_folders_do_not_consume_document_reference_limit(
        self,
        test_db: Session,
        test_user: User,
    ) -> None:
        """The backend limit counts materialized documents rather than folders."""
        folders = [
            _create_synced_node(
                test_db,
                test_user.id,
                node_id=f"folder-limit-{index}",
                name=f"Folder {index}",
                node_type="folder",
                content_type="",
            )
            for index in range(2)
        ]
        document = _create_synced_node(
            test_db,
            test_user.id,
            node_id="document-with-folders",
        )
        uploaded_context = MagicMock(id=322, type_data={})

        with (
            patch(
                "app.services.dingtalk_doc_materialization_service."
                "settings.DINGTALK_DOC_MAX_REFERENCES",
                1,
            ),
            patch(
                "app.services.dingtalk_doc_materialization_service."
                "dingtalk_dws_service.doc_info",
                new=AsyncMock(
                    return_value={"contentType": "ALIDOC", "extension": "adoc"}
                ),
            ),
            patch(
                "app.services.dingtalk_doc_materialization_service."
                "dingtalk_dws_service.doc_read",
                new=AsyncMock(return_value={"markdown": "Body"}),
            ),
            patch(
                "app.services.dingtalk_doc_materialization_service."
                "context_service.upload_attachment",
                return_value=(uploaded_context, None),
            ),
            patch.object(test_db, "refresh", return_value=None),
        ):
            attachment_ids = (
                await DingTalkDocMaterializationService.materialize_contexts(
                    db=test_db,
                    user_id=test_user.id,
                    contexts=[
                        *[_context_payload(folder) for folder in folders],
                        _context_payload(document),
                    ],
                )
            )

        assert attachment_ids == [322]

    def test_online_document_title_extension_does_not_force_binary_download(
        self,
    ) -> None:
        """An online document named like a file still materializes as Markdown."""
        node = MagicMock()
        node.name = "Architecture.pdf"
        node.content_type = "ALIDOC"

        kind = DingTalkDocMaterializationService._document_kind(
            {"contentType": "ALIDOC"},
            node,
        )

        assert kind == "adoc"

    @pytest.mark.asyncio
    async def test_materializes_axls_workbook_as_markdown_attachment(
        self,
        test_db: Session,
        test_user: User,
    ) -> None:
        """Every worksheet in an ALIDOC/axls workbook is included as CSV."""
        node = _create_synced_node(
            test_db,
            test_user.id,
            node_id="sheet-node-1",
            name="Image Models",
        )
        uploaded_context = MagicMock(id=654, type_data={})

        with (
            patch(
                "app.services.dingtalk_doc_materialization_service."
                "dingtalk_dws_service.doc_info",
                new=AsyncMock(
                    return_value={"contentType": "ALIDOC", "extension": "axls"}
                ),
            ),
            patch(
                "app.services.dingtalk_doc_materialization_service."
                "dingtalk_dws_service.doc_read",
                new=AsyncMock(),
            ) as mock_doc_read,
            patch(
                "app.services.dingtalk_doc_materialization_service."
                "dingtalk_dws_service.sheet_list",
                new=AsyncMock(
                    return_value=[
                        {"sheetId": "sheet-1", "name": "Models"},
                        {"id": "sheet-2", "title": "Empty Sheet"},
                    ]
                ),
            ) as mock_sheet_list,
            patch(
                "app.services.dingtalk_doc_materialization_service."
                "dingtalk_dws_service.sheet_csv_get",
                new=AsyncMock(
                    side_effect=[
                        {
                            "csv": "[row=1]Model,Vendor\n[row=2]Flux,Black Forest",
                            "hasMore": True,
                        },
                        {"csv": "", "hasMore": False},
                    ]
                ),
            ) as mock_csv_get,
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

        assert attachment_ids == [654]
        mock_doc_read.assert_not_awaited()
        mock_sheet_list.assert_awaited_once_with(test_user.id, node.dingtalk_node_id)
        assert mock_csv_get.await_args_list[0].args == (
            test_user.id,
            node.dingtalk_node_id,
            "sheet-1",
        )
        assert mock_csv_get.await_args_list[0].kwargs == {"max_chars": 200000}
        assert mock_csv_get.await_count == 2
        body = mock_upload.call_args.kwargs["binary_data"].decode("utf-8")
        assert "## 工作表: Models" in body
        assert "[row=2]Flux,Black Forest" in body
        assert "引用内容已截断" in body
        assert "## 工作表: Empty Sheet" in body
        assert "该工作表没有可读取的数据" in body

    @pytest.mark.asyncio
    async def test_materializes_able_base_as_markdown_attachment(
        self,
        test_db: Session,
        test_user: User,
    ) -> None:
        """AI table fields and records are rendered with human-readable names."""
        node = _create_synced_node(
            test_db,
            test_user.id,
            node_id="base-1",
            name="Model Catalog",
        )
        uploaded_context = MagicMock(id=777, type_data={})
        table_details = [
            {
                "tableId": "table-1",
                "tableName": "Models",
                "fields": [
                    {"fieldId": "fld-name", "fieldName": "Model"},
                    {"fieldId": "fld-vendor", "fieldName": "Vendor"},
                ],
            },
            {
                "tableId": "table-2",
                "tableName": "Empty Data",
                "fields": [],
            },
        ]

        with (
            patch(
                "app.services.dingtalk_doc_materialization_service."
                "dingtalk_dws_service.doc_info",
                new=AsyncMock(
                    return_value={"contentType": "ALIDOC", "extension": "able"}
                ),
            ),
            patch(
                "app.services.dingtalk_doc_materialization_service."
                "dingtalk_dws_service.aitable_base_get",
                new=AsyncMock(
                    return_value={
                        "baseName": "Model Catalog",
                        "tables": [
                            {"tableId": "table-1"},
                            {"tableId": "table-2"},
                        ],
                    }
                ),
            ) as mock_base_get,
            patch(
                "app.services.dingtalk_doc_materialization_service."
                "dingtalk_dws_service.aitable_table_get",
                new=AsyncMock(return_value=table_details),
            ) as mock_table_get,
            patch(
                "app.services.dingtalk_doc_materialization_service."
                "dingtalk_dws_service.aitable_record_query",
                new=AsyncMock(
                    side_effect=[
                        {
                            "records": [
                                {
                                    "recordId": "record-1",
                                    "cells": {
                                        "fld-name": "Flux",
                                        "fld-vendor": {"name": "Black Forest Labs"},
                                    },
                                }
                            ],
                            "hasMore": True,
                            "cursor": "next-page",
                        },
                        {
                            "records": [
                                {
                                    "recordId": "record-2",
                                    "cells": {
                                        "fld-name": "SDXL",
                                        "fld-vendor": "Stability AI",
                                    },
                                }
                            ],
                            "hasMore": False,
                        },
                        {"records": [], "hasMore": False},
                    ]
                ),
            ) as mock_record_query,
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

        assert attachment_ids == [777]
        mock_base_get.assert_awaited_once_with(test_user.id, node.dingtalk_node_id)
        mock_table_get.assert_awaited_once_with(
            test_user.id,
            node.dingtalk_node_id,
            ["table-1", "table-2"],
        )
        first_query = mock_record_query.await_args_list[0]
        assert first_query.args == (test_user.id, node.dingtalk_node_id, "table-1")
        assert first_query.kwargs == {
            "field_ids": ["fld-name", "fld-vendor"],
            "page_limit": 50,
        }
        second_query = mock_record_query.await_args_list[1]
        assert second_query.args == (test_user.id, node.dingtalk_node_id, "table-1")
        assert second_query.kwargs == {
            "field_ids": ["fld-name", "fld-vendor"],
            "page_limit": 50,
            "cursor": "next-page",
        }
        assert mock_record_query.await_count == 3
        body = mock_upload.call_args.kwargs["binary_data"].decode("utf-8")
        assert "## 数据表: Models" in body
        assert "recordId,Model,Vendor" in body
        assert "record-1,Flux,Black Forest Labs" in body
        assert "record-2,SDXL,Stability AI" in body
        assert "## 数据表: Empty Data" in body
        assert "该数据表没有可读取的记录" in body
        assert uploaded_context.type_data["dingtalk_document_kind"] == "able"

    @pytest.mark.asyncio
    async def test_limits_aitable_to_first_fifty_sheets(self) -> None:
        """AI table schema batching reads the first fifty sheets in order."""
        summaries = [{"tableId": f"table-{index}"} for index in range(55)]

        async def get_tables(
            user_id: int,
            base_id: str,
            table_ids: list[str],
        ) -> list[dict[str, str]]:
            assert user_id == 42
            assert base_id == "base-1"
            return [{"tableId": table_id} for table_id in table_ids]

        with patch(
            "app.services.dingtalk_doc_materialization_service."
            "dingtalk_dws_service.aitable_table_get",
            new=AsyncMock(side_effect=get_tables),
        ) as mock_table_get:
            tables = await DingTalkDocMaterializationService._load_aitable_tables(
                42,
                "base-1",
                summaries,
            )

        assert [table["tableId"] for table in tables] == [
            f"table-{index}" for index in range(50)
        ]
        assert mock_table_get.await_count == 5
        assert all(len(call.args[2]) == 10 for call in mock_table_get.await_args_list)

    @pytest.mark.asyncio
    async def test_aitable_marks_sheets_after_first_fifty_as_omitted(self) -> None:
        """AI table references explain when sheets after fifty are omitted."""
        node = MagicMock(dingtalk_node_id="base-1", name="Large Base")
        summaries = [{"tableId": f"table-{index}"} for index in range(51)]

        with (
            patch(
                "app.services.dingtalk_doc_materialization_service."
                "dingtalk_dws_service.aitable_base_get",
                new=AsyncMock(return_value={"tables": summaries}),
            ),
            patch.object(
                DingTalkDocMaterializationService,
                "_load_aitable_tables",
                new=AsyncMock(return_value=[]),
            ),
        ):
            markdown = await DingTalkDocMaterializationService._read_aitable(
                42,
                node,
            )

        assert "超过 50 个数据表" in markdown
        assert "仅引用前 50 个" in markdown

    @pytest.mark.asyncio
    async def test_aitable_table_truncates_at_record_limit(
        self,
    ) -> None:
        """Streaming stops exactly at the configured per-table record limit."""
        output = BoundedTextBuilder(1024 * 1024)
        payload = {
            "records": [
                {"recordId": "record-1", "cells": {}},
                {"recordId": "record-2", "cells": {}},
            ],
            "hasMore": False,
        }

        with (
            patch(
                "app.services.dingtalk_doc_materialization_service."
                "AITABLE_MAX_RECORDS_PER_TABLE",
                1,
            ),
            patch(
                "app.services.dingtalk_doc_materialization_service."
                "dingtalk_dws_service.aitable_record_query",
                new=AsyncMock(return_value=payload),
            ) as mock_record_query,
        ):
            complete = await DingTalkDocMaterializationService._write_aitable_table(
                output=output,
                user_id=42,
                base_id="base-1",
                table_id="table-1",
                table={"tableId": "table-1", "tableName": "Large Table"},
                field_ids=[],
                index=1,
            )

        assert complete is True
        mock_record_query.assert_awaited_once_with(
            42,
            "base-1",
            "table-1",
            field_ids=[],
            page_limit=1,
        )
        markdown = output.getvalue()
        assert "record-1" in markdown
        assert "record-2" not in markdown
        assert "超过 1 条记录" in markdown

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("extension", "binary_data"),
        [
            ("pdf", b"%PDF-1.7 test"),
            ("docx", b"PK test word document"),
            ("pptx", b"PK test presentation"),
            ("xlsx", b"PK test workbook"),
            ("txt", b"Plain text document"),
            ("md", b"# Markdown document"),
        ],
    )
    async def test_downloads_binary_documents_as_original_attachments(
        self,
        test_db: Session,
        test_user: User,
        extension: str,
        binary_data: bytes,
    ) -> None:
        """PDF and uploaded xlsx nodes retain their original attachment format."""
        node = _create_synced_node(
            test_db,
            test_user.id,
            node_id=f"{extension}-node",
            name="Quarterly Report",
            node_type="file",
            content_type="DOCUMENT",
        )
        uploaded_context = MagicMock(id=888, type_data={})

        async def download_file(user_id: int, node_id: str, output: str) -> None:
            assert user_id == test_user.id
            assert node_id == node.dingtalk_node_id
            Path(output).write_bytes(binary_data)

        with (
            patch(
                "app.services.dingtalk_doc_materialization_service."
                "dingtalk_dws_service.doc_info",
                new=AsyncMock(
                    return_value={
                        "contentType": "DOCUMENT",
                        "extension": f".{extension}",
                    }
                ),
            ),
            patch(
                "app.services.dingtalk_doc_materialization_service."
                "dingtalk_dws_service.doc_download",
                new=AsyncMock(side_effect=download_file),
            ) as mock_download,
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

        assert attachment_ids == [888]
        assert mock_download.await_count == 1
        upload_kwargs = mock_upload.call_args.kwargs
        assert upload_kwargs["filename"] == f"Quarterly Report.{extension}"
        assert upload_kwargs["binary_data"] == binary_data
        assert uploaded_context.type_data["dingtalk_document_kind"] == extension

    @pytest.mark.asyncio
    async def test_team_file_uses_drive_metadata_and_download(
        self,
        test_db: Session,
        test_user: User,
    ) -> None:
        """Team-space files are downloaded through drive with their space ID."""
        node = _create_synced_node(
            test_db,
            test_user.id,
            node_id="team-file-1",
            name="Team Report.pdf",
            node_type="file",
            source=DingTalkNodeSource.TEAM_FILES.value,
            content_type="pdf",
        )
        uploaded_context = MagicMock(id=889, type_data={})

        async def download_file(
            user_id: int,
            node_id: str,
            output: str,
            *,
            space_id: str | None = None,
        ) -> None:
            assert user_id == test_user.id
            assert node_id == node.dingtalk_node_id
            assert space_id == "WS1"
            Path(output).write_bytes(b"%PDF team file")

        with (
            patch(
                "app.services.dingtalk_doc_materialization_service."
                "dingtalk_dws_service.drive_info",
                new=AsyncMock(
                    return_value={"contentType": "DOCUMENT", "extension": "pdf"}
                ),
            ) as mock_info,
            patch(
                "app.services.dingtalk_doc_materialization_service."
                "dingtalk_dws_service.drive_download",
                new=AsyncMock(side_effect=download_file),
            ) as mock_download,
            patch(
                "app.services.dingtalk_doc_materialization_service."
                "dingtalk_dws_service.doc_download",
                new=AsyncMock(),
            ) as mock_doc_download,
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

        assert attachment_ids == [889]
        mock_info.assert_awaited_once_with(
            test_user.id,
            node.dingtalk_node_id,
            space_id="WS1",
        )
        mock_download.assert_awaited_once()
        mock_doc_download.assert_not_awaited()
        assert mock_upload.call_args.kwargs["binary_data"] == b"%PDF team file"
        assert uploaded_context.type_data["dingtalk_source"] == "team_files"

    @pytest.mark.asyncio
    async def test_rejects_axls_workbook_without_worksheets(
        self,
        test_db: Session,
        test_user: User,
    ) -> None:
        """A workbook with no worksheet metadata cannot be materialized."""
        node = _create_synced_node(
            test_db,
            test_user.id,
            node_id="empty-workbook",
            name="Empty Workbook",
        )

        with (
            patch(
                "app.services.dingtalk_doc_materialization_service."
                "dingtalk_dws_service.doc_info",
                new=AsyncMock(
                    return_value={"contentType": "ALIDOC", "extension": "axls"}
                ),
            ),
            patch(
                "app.services.dingtalk_doc_materialization_service."
                "dingtalk_dws_service.sheet_list",
                new=AsyncMock(return_value=[]),
            ),
        ):
            with pytest.raises(ValueError, match="has no worksheets"):
                await DingTalkDocMaterializationService.materialize_contexts(
                    db=test_db,
                    user_id=test_user.id,
                    contexts=[_context_payload(node)],
                )

    @pytest.mark.asyncio
    async def test_rejects_unsupported_document_types(
        self,
        test_db: Session,
        test_user: User,
    ) -> None:
        """AI tables are not treated as ALIDOC online spreadsheets."""
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
            with pytest.raises(ValueError, match="not a supported document"):
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
