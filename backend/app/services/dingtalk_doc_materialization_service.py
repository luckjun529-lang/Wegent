# SPDX-FileCopyrightText: 2026 Weibo, Inc.
#
# SPDX-License-Identifier: Apache-2.0

"""Materialize selected DingTalk documents into chat attachments."""

from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.dingtalk_doc import DingTalkNodeSource, DingtalkSyncedNode
from app.services.context import context_service
from app.services.dingtalk_dws_service import dingtalk_dws_service

FILENAME_UNSAFE_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')
SPREADSHEET_MAX_CHARS = 200000
SPREADSHEET_MAX_SHEETS = 50
AITABLE_MAX_RECORDS_PER_TABLE = 100000
AITABLE_RECORD_PAGE_SIZE = 100
AITABLE_DWS_CHUNK_PAGE_LIMIT = 50
AITABLE_MAX_TABLES = 50
AITABLE_TABLE_BATCH_SIZE = 10
AITABLE_FIELD_LIMIT = 100
AITABLE_OUTPUT_RESERVE_BYTES = 256 * 1024
AITABLE_NOTICE_RESERVE_BYTES = 64 * 1024
DOWNLOADABLE_EXTENSIONS = frozenset({"pdf", "docx", "pptx", "xlsx", "csv", "txt", "md"})
DINGTALK_NODE_SOURCES = frozenset(item.value for item in DingTalkNodeSource)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DingtalkDocReference:
    """A DingTalk document selected in the frontend composer."""

    source: str
    dingtalk_node_id: str
    doc_url: str
    name: str
    node_type: str


@dataclass(frozen=True)
class MaterializedDingTalkFile:
    """File content produced from a DingTalk node for attachment upload."""

    filename: str
    binary_data: bytes
    document_kind: str


class BoundedTextBuilder:
    """Build UTF-8 text without exceeding an attachment-size budget."""

    def __init__(self, max_bytes: int) -> None:
        self._max_bytes = max_bytes
        self._size_bytes = 0
        self._output = io.StringIO()

    def append(self, value: str, *, reserve_bytes: int = 0) -> bool:
        encoded_size = len(value.encode("utf-8"))
        available = self._max_bytes - self._size_bytes - reserve_bytes
        if encoded_size > max(available, 0):
            return False
        self._output.write(value)
        self._size_bytes += encoded_size
        return True

    def getvalue(self) -> str:
        return self._output.getvalue()


class DingTalkDocMaterializationService:
    """Read selected DingTalk docs with DWS and store them as attachments."""

    @classmethod
    async def materialize_contexts(
        cls,
        *,
        db: Session,
        user_id: int,
        contexts: list[Any] | None,
    ) -> list[int]:
        references = cls._extract_references(contexts)
        if not references:
            return []

        nodes = cls._get_owned_nodes(db, user_id, references)
        document_nodes = [node for node in nodes if node.node_type != "folder"]
        if len(document_nodes) > settings.DINGTALK_DOC_MAX_REFERENCES:
            raise ValueError(
                f"At most {settings.DINGTALK_DOC_MAX_REFERENCES} DingTalk documents "
                "can be referenced in one message"
            )
        attachment_ids: list[int] = []
        try:
            for node in document_nodes:
                info = await cls._load_node_info(user_id, node)
                read_at = datetime.now(timezone.utc).isoformat()
                materialized = await cls._materialize_node(
                    user_id=user_id,
                    node=node,
                    info=info,
                    read_at=read_at,
                )
                context, _ = context_service.upload_attachment(
                    db=db,
                    user_id=user_id,
                    filename=materialized.filename,
                    binary_data=materialized.binary_data,
                    subtask_id=0,
                )
                attachment_ids.append(context.id)
                context.type_data = {
                    **(context.type_data or {}),
                    "source": "dingtalk_doc",
                    "dingtalk_node_id": node.dingtalk_node_id,
                    "doc_url": node.doc_url,
                    "dingtalk_source": node.source,
                    "read_at": read_at,
                    "dingtalk_document_kind": materialized.document_kind,
                }
                db.commit()
                db.refresh(context)
        except Exception:
            cls.delete_unlinked_attachments(db, user_id, attachment_ids)
            raise

        return attachment_ids

    @classmethod
    def filter_non_dingtalk_contexts(
        cls, contexts: list[Any] | None
    ) -> list[Any] | None:
        if not contexts:
            return contexts
        filtered = [ctx for ctx in contexts if cls._ctx_type(ctx) != "dingtalk_doc"]
        return filtered or None

    @classmethod
    def _extract_references(
        cls, contexts: list[Any] | None
    ) -> list[DingtalkDocReference]:
        if not contexts:
            return []

        references: list[DingtalkDocReference] = []
        seen: set[tuple[str, str]] = set()
        for ctx in contexts:
            if cls._ctx_type(ctx) != "dingtalk_doc":
                continue
            data = cls._ctx_data(ctx)
            source = str(data.get("source") or DingTalkNodeSource.DOCS.value)
            if source not in DINGTALK_NODE_SOURCES:
                raise ValueError(f"Unsupported DingTalk node source: {source}")
            node_id = str(data.get("dingtalk_node_id") or "").strip()
            if not node_id:
                raise ValueError(
                    "DingTalk document context is missing dingtalk_node_id"
                )
            key = (source, node_id)
            if key in seen:
                continue
            seen.add(key)
            references.append(
                DingtalkDocReference(
                    source=source,
                    dingtalk_node_id=node_id,
                    doc_url=str(data.get("doc_url") or ""),
                    name=str(data.get("name") or node_id),
                    node_type=str(data.get("node_type") or "doc"),
                )
            )
        return references

    @staticmethod
    def _ctx_type(ctx: Any) -> str | None:
        if isinstance(ctx, dict):
            return ctx.get("type")
        return getattr(ctx, "type", None)

    @staticmethod
    def _ctx_data(ctx: Any) -> dict[str, Any]:
        if isinstance(ctx, dict):
            data = ctx.get("data")
        else:
            data = getattr(ctx, "data", None)
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _get_owned_nodes(
        db: Session,
        user_id: int,
        references: list[DingtalkDocReference],
    ) -> list[DingtalkSyncedNode]:
        node_ids = [reference.dingtalk_node_id for reference in references]
        sources = [reference.source for reference in references]
        rows = (
            db.query(DingtalkSyncedNode)
            .filter(
                DingtalkSyncedNode.user_id == user_id,
                DingtalkSyncedNode.source.in_(sources),
                DingtalkSyncedNode.dingtalk_node_id.in_(node_ids),
                DingtalkSyncedNode.is_active == True,  # noqa: E712
            )
            .all()
        )
        by_key = {(row.source, row.dingtalk_node_id): row for row in rows}
        owned_nodes: list[DingtalkSyncedNode] = []
        for reference in references:
            node = by_key.get((reference.source, reference.dingtalk_node_id))
            if not node:
                raise ValueError("DingTalk document is not synced for the current user")
            owned_nodes.append(node)
        return owned_nodes

    @staticmethod
    def delete_unlinked_attachments(
        db: Session,
        user_id: int,
        attachment_ids: list[int],
    ) -> None:
        """Delete materialized attachments that have not been linked to a message."""
        for attachment_id in reversed(attachment_ids):
            try:
                context_service.delete_context(db, attachment_id, user_id)
            except Exception:
                logger.exception(
                    "Failed to clean up unlinked DingTalk attachment %s",
                    attachment_id,
                )

    @staticmethod
    async def _load_node_info(
        user_id: int,
        node: DingtalkSyncedNode,
    ) -> dict[str, Any]:
        if node.source == DingTalkNodeSource.TEAM_FILES.value:
            return await dingtalk_dws_service.drive_info(
                user_id,
                node.dingtalk_node_id,
                space_id=node.workspace_id or None,
            )
        return await dingtalk_dws_service.doc_info(user_id, node.dingtalk_node_id)

    @classmethod
    async def _materialize_node(
        cls,
        *,
        user_id: int,
        node: DingtalkSyncedNode,
        info: dict[str, Any],
        read_at: str,
    ) -> MaterializedDingTalkFile:
        document_kind = cls._document_kind(info, node)
        if document_kind in DOWNLOADABLE_EXTENSIONS:
            return await cls._download_binary_file(user_id, node, document_kind)
        if document_kind not in {"adoc", "axls", "able"}:
            raise ValueError(
                f"DingTalk document '{node.name}' is not a supported document"
            )

        markdown = await cls._read_markdown(user_id, node, document_kind)
        if not markdown.strip():
            raise ValueError(f"DingTalk document '{node.name}' returned empty content")
        body = cls._build_markdown_attachment(
            title=node.name,
            doc_url=node.doc_url,
            node_id=node.dingtalk_node_id,
            read_at=read_at,
            markdown=markdown,
        )
        return MaterializedDingTalkFile(
            filename=cls._filename_with_extension(node.name, "md"),
            binary_data=body.encode("utf-8"),
            document_kind=document_kind,
        )

    @classmethod
    async def _read_markdown(
        cls,
        user_id: int,
        node: DingtalkSyncedNode,
        document_kind: str,
    ) -> str:
        if document_kind == "adoc":
            payload = await dingtalk_dws_service.doc_read(
                user_id,
                node.dingtalk_node_id,
            )
            return cls._extract_markdown(payload)
        if document_kind == "axls":
            return await cls._read_spreadsheet(user_id, node)
        if document_kind == "able":
            return await cls._read_aitable(user_id, node)
        return ""

    @classmethod
    def _document_kind(
        cls,
        info: dict[str, Any],
        node: DingtalkSyncedNode,
    ) -> str | None:
        content_type = str(
            cls._first_value(info, "contentType", "content_type", "type")
            or node.content_type
            or ""
        ).upper()
        extension_value = cls._first_value(
            info,
            "extension",
            "fileExtension",
            "file_extension",
        )
        extension = (
            str(extension_value or Path(node.name).suffix.lstrip("."))
            .lower()
            .lstrip(".")
        )

        if extension in DOWNLOADABLE_EXTENSIONS:
            return extension if content_type not in {"ALIDOC", "ADOC"} else "adoc"
        if extension == "able" and content_type in {
            "",
            "ALIDOC",
            "AIMTABLE",
            "AITABLE",
        }:
            return extension
        if extension in {"adoc", "axls"} and content_type in {
            "",
            "ALIDOC",
            "ADOC",
        }:
            return extension
        if extension:
            return None
        if content_type in {"ALIDOC", "ADOC"}:
            return "adoc"
        return None

    @classmethod
    async def _download_binary_file(
        cls,
        user_id: int,
        node: DingtalkSyncedNode,
        extension: str,
    ) -> MaterializedDingTalkFile:
        filename = cls._filename_with_extension(node.name, extension)
        with tempfile.TemporaryDirectory(prefix="wegent-dingtalk-") as temp_dir:
            output_path = Path(temp_dir) / filename
            if node.source == DingTalkNodeSource.TEAM_FILES.value:
                await dingtalk_dws_service.drive_download(
                    user_id,
                    node.dingtalk_node_id,
                    str(output_path),
                    space_id=node.workspace_id or None,
                )
            else:
                await dingtalk_dws_service.doc_download(
                    user_id,
                    node.dingtalk_node_id,
                    str(output_path),
                )
            if not output_path.is_file():
                raise ValueError(
                    f"DingTalk file '{node.name}' was not downloaded successfully"
                )
            max_size = settings.MAX_UPLOAD_FILE_SIZE_MB * 1024 * 1024
            if output_path.stat().st_size > max_size:
                raise ValueError(
                    f"DingTalk file '{node.name}' exceeds the "
                    f"{settings.MAX_UPLOAD_FILE_SIZE_MB} MB upload limit"
                )
            binary_data = await asyncio.to_thread(output_path.read_bytes)
        if not binary_data:
            raise ValueError(f"DingTalk file '{node.name}' returned empty content")
        return MaterializedDingTalkFile(
            filename=filename,
            binary_data=binary_data,
            document_kind=extension,
        )

    @classmethod
    async def _read_spreadsheet(
        cls,
        user_id: int,
        node: DingtalkSyncedNode,
    ) -> str:
        sheets = await dingtalk_dws_service.sheet_list(
            user_id,
            node.dingtalk_node_id,
        )
        if not sheets:
            raise ValueError(f"DingTalk spreadsheet '{node.name}' has no worksheets")

        sections: list[str] = []
        for index, sheet in enumerate(sheets[:SPREADSHEET_MAX_SHEETS], start=1):
            sheet_id = cls._first_value(sheet, "sheetId", "sheet_id", "id")
            if sheet_id in (None, ""):
                raise ValueError(
                    f"DingTalk spreadsheet '{node.name}' returned a worksheet "
                    "without an ID"
                )
            sheet_name = cls._worksheet_name(sheet, index)
            payload = await dingtalk_dws_service.sheet_csv_get(
                user_id,
                node.dingtalk_node_id,
                str(sheet_id),
                max_chars=SPREADSHEET_MAX_CHARS,
            )
            sections.append(cls._spreadsheet_section(sheet_name, payload))
        if len(sheets) > SPREADSHEET_MAX_SHEETS:
            sections.append(
                f"> 注意：该在线表格超过 {SPREADSHEET_MAX_SHEETS} 个工作表，"
                f"仅引用前 {SPREADSHEET_MAX_SHEETS} 个。"
            )
        return "\n\n".join(sections)

    @classmethod
    async def _read_aitable(
        cls,
        user_id: int,
        node: DingtalkSyncedNode,
    ) -> str:
        base_id = node.dingtalk_node_id
        base = await dingtalk_dws_service.aitable_base_get(user_id, base_id)
        table_summaries = cls._extract_dict_list(base, "tables")
        if not table_summaries:
            raise ValueError(f"DingTalk AI table '{node.name}' has no data tables")

        tables = await cls._load_aitable_tables(user_id, base_id, table_summaries)
        max_output_bytes = max(
            1024,
            settings.MAX_UPLOAD_FILE_SIZE_MB * 1024 * 1024
            - AITABLE_OUTPUT_RESERVE_BYTES,
        )
        output = BoundedTextBuilder(max_output_bytes)
        output_truncated = False
        for index, table in enumerate(tables, start=1):
            table_id = cls._first_value(table, "tableId", "table_id", "id")
            if table_id in (None, ""):
                raise ValueError(
                    f"DingTalk AI table '{node.name}' returned a table without an ID"
                )
            fields = cls._extract_dict_list(table, "fields")
            field_ids = cls._aitable_field_ids(fields)
            if index > 1 and not output.append("\n\n"):
                output_truncated = True
                break
            table_complete = await cls._write_aitable_table(
                output=output,
                user_id=user_id,
                base_id=base_id,
                table_id=str(table_id),
                table=table,
                field_ids=field_ids,
                index=index,
            )
            if not table_complete:
                output_truncated = True
                break
        if output_truncated:
            output.append(
                "\n\n> 注意：AI 表格引用达到附件大小限制，后续记录或数据表未读取。"
            )
        if len(table_summaries) > AITABLE_MAX_TABLES:
            output.append(
                f"\n\n> 注意：该 AI 表格超过 {AITABLE_MAX_TABLES} 个数据表，"
                f"仅引用前 {AITABLE_MAX_TABLES} 个。"
            )
        return output.getvalue()

    @classmethod
    async def _write_aitable_table(
        cls,
        *,
        output: BoundedTextBuilder,
        user_id: int,
        base_id: str,
        table_id: str,
        table: dict[str, Any],
        field_ids: list[str],
        index: int,
    ) -> bool:
        """Stream bounded DWS chunks into one Markdown CSV section."""
        table_name = cls._display_name(
            cls._first_value(table, "tableName", "table_name", "name", "title"),
            f"Table {index}",
        )
        if not output.append(f"## 数据表: {table_name}\n\n"):
            return False

        csv_field_ids = list(field_ids)
        field_names = cls._aitable_field_names(table)
        query_field_ids = field_ids if len(field_ids) <= AITABLE_FIELD_LIMIT else None
        cursor: str | None = None
        seen_cursors: set[str] = set()
        record_count = 0
        wrote_csv = False
        record_limit_reached = False
        source_incomplete = False

        while record_count < AITABLE_MAX_RECORDS_PER_TABLE:
            remaining_records = AITABLE_MAX_RECORDS_PER_TABLE - record_count
            page_limit = min(
                AITABLE_DWS_CHUNK_PAGE_LIMIT,
                max(
                    1,
                    (remaining_records + AITABLE_RECORD_PAGE_SIZE - 1)
                    // AITABLE_RECORD_PAGE_SIZE,
                ),
            )
            query_kwargs: dict[str, Any] = {
                "field_ids": query_field_ids,
                "page_limit": page_limit,
            }
            if cursor:
                query_kwargs["cursor"] = cursor
            payload = await dingtalk_dws_service.aitable_record_query(
                user_id,
                base_id,
                table_id,
                **query_kwargs,
            )
            records = cls._extract_dict_list(payload, "records", "items")
            if records and not wrote_csv:
                cls._extend_aitable_field_ids(csv_field_ids, records)
                header = cls._aitable_csv_row(
                    [
                        "recordId",
                        *[field_names.get(item, item) for item in csv_field_ids],
                    ]
                )
                if not output.append(
                    cls._indent_code_block(header),
                    reserve_bytes=AITABLE_NOTICE_RESERVE_BYTES,
                ):
                    return False
                wrote_csv = True

            for record in records[:remaining_records]:
                row = cls._aitable_record_csv_row(record, csv_field_ids)
                if not output.append(
                    cls._indent_code_block(row),
                    reserve_bytes=AITABLE_NOTICE_RESERVE_BYTES,
                ):
                    return False
                record_count += 1

            has_more = cls._is_true(cls._first_value(payload, "hasMore", "has_more"))
            if len(records) > remaining_records or (
                record_count >= AITABLE_MAX_RECORDS_PER_TABLE and has_more
            ):
                record_limit_reached = True
                break
            if not has_more:
                break

            next_cursor = cls._first_value(
                payload,
                "cursor",
                "nextCursor",
                "next_cursor",
                "nextToken",
                "next_token",
            )
            if next_cursor in (None, ""):
                source_incomplete = True
                break
            cursor = str(next_cursor)
            if cursor in seen_cursors:
                raise ValueError(
                    f"DingTalk AI table '{table_name}' returned a repeated cursor"
                )
            seen_cursors.add(cursor)

        if not wrote_csv:
            output.append("*该数据表没有可读取的记录。*")
        if record_limit_reached:
            output.append(
                f"\n> 注意：该数据表超过 {AITABLE_MAX_RECORDS_PER_TABLE} 条记录，"
                "引用内容已截断。"
            )
        elif source_incomplete:
            output.append("\n> 注意：DWS 未返回后续游标，引用内容已截断。")
        return True

    @classmethod
    async def _load_aitable_tables(
        cls,
        user_id: int,
        base_id: str,
        table_summaries: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        table_ids: list[str] = []
        for summary in table_summaries[:AITABLE_MAX_TABLES]:
            table_id = cls._first_value(summary, "tableId", "table_id", "id")
            if table_id not in (None, ""):
                table_ids.append(str(table_id))
        if not table_ids:
            raise ValueError("DingTalk AI table directory returned no table IDs")

        tables: list[dict[str, Any]] = []
        for start in range(0, len(table_ids), AITABLE_TABLE_BATCH_SIZE):
            batch = table_ids[start : start + AITABLE_TABLE_BATCH_SIZE]
            tables.extend(
                await dingtalk_dws_service.aitable_table_get(
                    user_id,
                    base_id,
                    batch,
                )
            )
        if not tables:
            raise ValueError("DingTalk AI table returned no readable table schemas")
        return tables

    @classmethod
    def _aitable_field_names(cls, table: dict[str, Any]) -> dict[str, str]:
        fields = cls._extract_dict_list(table, "fields")
        return {
            str(
                cls._first_value(field, "fieldId", "field_id", "id")
            ): cls._display_name(
                cls._first_value(field, "fieldName", "field_name", "name"),
                str(cls._first_value(field, "fieldId", "field_id", "id") or "Field"),
            )
            for field in fields
            if cls._first_value(field, "fieldId", "field_id", "id") not in (None, "")
        }

    @staticmethod
    def _extend_aitable_field_ids(
        field_ids: list[str],
        records: list[dict[str, Any]],
    ) -> None:
        known_ids = set(field_ids)
        for record in records:
            cells = record.get("cells")
            if not isinstance(cells, dict):
                continue
            for field_id in cells:
                normalized = str(field_id)
                if normalized not in known_ids:
                    field_ids.append(normalized)
                    known_ids.add(normalized)

    @staticmethod
    def _aitable_csv_row(values: list[Any]) -> str:
        output = io.StringIO(newline="")
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(values)
        return output.getvalue()

    @staticmethod
    def _indent_code_block(value: str) -> str:
        return "".join(f"    {line}" for line in value.splitlines(keepends=True))

    @classmethod
    def _aitable_record_csv_row(
        cls,
        record: dict[str, Any],
        field_ids: list[str],
    ) -> str:
        cells = record.get("cells") if isinstance(record.get("cells"), dict) else {}
        record_id = cls._first_value(record, "recordId", "record_id", "id") or ""
        return cls._aitable_csv_row(
            [
                record_id,
                *[
                    cls._format_aitable_cell(cells.get(field_id))
                    for field_id in field_ids
                ],
            ]
        )

    @classmethod
    def _aitable_field_ids(cls, fields: list[dict[str, Any]]) -> list[str]:
        result: list[str] = []
        for field in fields:
            field_id = cls._first_value(field, "fieldId", "field_id", "id")
            if field_id not in (None, ""):
                result.append(str(field_id))
        return result

    @classmethod
    def _format_aitable_cell(cls, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (str, int, float)):
            return str(value)
        if isinstance(value, list):
            return "; ".join(cls._format_aitable_cell(item) for item in value)
        if isinstance(value, dict):
            text = value.get("text") or value.get("name") or value.get("filename")
            link = value.get("link") or value.get("url")
            if text and link:
                return f"{text} ({link})"
            if text:
                return str(text)
            if isinstance(value.get("markdown"), str):
                return value["markdown"]
            linked_ids = value.get("linkedRecordIds")
            if isinstance(linked_ids, list):
                return "; ".join(str(item) for item in linked_ids)
            return json.dumps(
                value, ensure_ascii=False, separators=(",", ":"), default=str
            )
        return str(value)

    @staticmethod
    def _is_true(value: Any) -> bool:
        return value is True or str(value).lower() == "true"

    @classmethod
    def _worksheet_name(cls, sheet: dict[str, Any], index: int) -> str:
        value = cls._first_value(sheet, "name", "title", "sheetName", "sheet_name")
        return cls._display_name(value, f"Sheet {index}")

    @classmethod
    def _spreadsheet_section(cls, sheet_name: str, payload: dict[str, Any]) -> str:
        csv_value = cls._first_value(payload, "csv")
        csv_text = csv_value if isinstance(csv_value, str) else ""
        lines = [f"## 工作表: {sheet_name}", ""]
        if csv_text.strip():
            fence = cls._code_fence(csv_text)
            lines.extend([f"{fence}csv", csv_text.rstrip(), fence])
        else:
            lines.append("*该工作表没有可读取的数据。*")

        has_more = cls._first_value(payload, "hasMore", "has_more")
        if has_more is True or str(has_more).lower() == "true":
            lines.extend(
                [
                    "",
                    f"> 注意：该工作表超过 {SPREADSHEET_MAX_CHARS} 个字符，"
                    "引用内容已截断。",
                ]
            )
        return "\n".join(lines)

    @staticmethod
    def _code_fence(content: str) -> str:
        longest_run = max(
            (len(match.group(0)) for match in re.finditer(r"`+", content)),
            default=0,
        )
        return "`" * max(3, longest_run + 1)

    @classmethod
    def _first_value(cls, data: Any, *keys: str) -> Any:
        if isinstance(data, dict):
            for key in keys:
                value = data.get(key)
                if value not in (None, ""):
                    return value
            for key in ("result", "data", "body"):
                value = data.get(key)
                if value is not None and value is not data:
                    nested = cls._first_value(value, *keys)
                    if nested not in (None, ""):
                        return nested
        return None

    @classmethod
    def _extract_dict_list(cls, data: Any, *keys: str) -> list[dict[str, Any]]:
        if isinstance(data, dict):
            for key in keys:
                value = data.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
            for key in ("result", "data", "body"):
                value = data.get(key)
                if value is not None and value is not data:
                    items = cls._extract_dict_list(value, *keys)
                    if items:
                        return items
        return []

    @staticmethod
    def _display_name(value: Any, fallback: str) -> str:
        name = str(value or fallback)
        return " ".join(name.splitlines()).strip() or fallback

    @classmethod
    def _extract_markdown(cls, payload: Any) -> str:
        if isinstance(payload, str):
            return payload
        if isinstance(payload, list):
            text_items = [item for item in payload if isinstance(item, str)]
            if text_items:
                return "\n".join(text_items)
            for item in payload:
                markdown = cls._extract_markdown(item)
                if markdown:
                    return markdown
            return ""
        if isinstance(payload, dict):
            for key in ("markdown", "content", "text"):
                value = payload.get(key)
                if isinstance(value, str):
                    return value
            for key in ("result", "data", "body", "document"):
                value = payload.get(key)
                if value is not None and value is not payload:
                    markdown = cls._extract_markdown(value)
                    if markdown:
                        return markdown
        return ""

    @staticmethod
    def _filename_with_extension(name: str, extension: str) -> str:
        safe = FILENAME_UNSAFE_RE.sub("_", name).strip().strip(".")
        if not safe:
            safe = "dingtalk-document"
        suffix = f".{extension.lower().lstrip('.')}"
        if not safe.lower().endswith(suffix):
            safe = f"{safe}{suffix}"
        if len(safe) <= 180:
            return safe
        return f"{safe[: 180 - len(suffix)]}{suffix}"

    @staticmethod
    def _build_markdown_attachment(
        *,
        title: str,
        doc_url: str,
        node_id: str,
        read_at: str,
        markdown: str,
    ) -> str:
        source_lines = [
            f"# {title}",
            "",
            f"Source: {doc_url}",
            f"DingTalk node: {node_id}",
            f"Read at: {read_at}",
            "",
            "---",
            "",
        ]
        return "\n".join(source_lines) + markdown


dingtalk_doc_materialization_service = DingTalkDocMaterializationService
