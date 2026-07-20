# SPDX-FileCopyrightText: 2026 Weibo, Inc.
#
# SPDX-License-Identifier: Apache-2.0

"""Materialize selected DingTalk documents into chat attachments."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.dingtalk_doc import DingTalkNodeSource, DingtalkSyncedNode
from app.services.context import context_service
from app.services.dingtalk_dws_service import dingtalk_dws_service

FILENAME_UNSAFE_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')


@dataclass(frozen=True)
class DingtalkDocReference:
    """A DingTalk document selected in the frontend composer."""

    source: str
    dingtalk_node_id: str
    doc_url: str
    name: str
    node_type: str


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
        if len(references) > settings.DINGTALK_DOC_MAX_REFERENCES:
            raise ValueError(
                f"At most {settings.DINGTALK_DOC_MAX_REFERENCES} DingTalk documents "
                "can be referenced in one message"
            )

        attachment_ids: list[int] = []
        for reference in references:
            node = cls._get_owned_node(db, user_id, reference)
            if node.node_type == "folder":
                continue

            info = await dingtalk_dws_service.doc_info(
                user_id,
                node.dingtalk_node_id,
            )
            if not cls._is_supported_adoc(info, node):
                raise ValueError(
                    f"DingTalk document '{node.name}' is not a supported text document"
                )

            read_payload = await dingtalk_dws_service.doc_read(
                user_id,
                node.dingtalk_node_id,
            )
            markdown = cls._extract_markdown(read_payload)
            if not markdown.strip():
                raise ValueError(
                    f"DingTalk document '{node.name}' returned empty content"
                )

            filename = cls._markdown_filename(node.name)
            read_at = datetime.now().isoformat()
            body = cls._build_markdown_attachment(
                title=node.name,
                doc_url=node.doc_url,
                node_id=node.dingtalk_node_id,
                read_at=read_at,
                markdown=markdown,
            )
            context, _ = context_service.upload_attachment(
                db=db,
                user_id=user_id,
                filename=filename,
                binary_data=body.encode("utf-8"),
                subtask_id=0,
            )
            context.type_data = {
                **(context.type_data or {}),
                "source": "dingtalk_doc",
                "dingtalk_node_id": node.dingtalk_node_id,
                "doc_url": node.doc_url,
                "dingtalk_source": node.source,
                "read_at": read_at,
            }
            db.commit()
            db.refresh(context)
            attachment_ids.append(context.id)

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
            if source not in {
                DingTalkNodeSource.DOCS.value,
                DingTalkNodeSource.WIKISPACE.value,
            }:
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
    def _get_owned_node(
        db: Session,
        user_id: int,
        reference: DingtalkDocReference,
    ) -> DingtalkSyncedNode:
        node = (
            db.query(DingtalkSyncedNode)
            .filter(
                DingtalkSyncedNode.user_id == user_id,
                DingtalkSyncedNode.source == reference.source,
                DingtalkSyncedNode.dingtalk_node_id == reference.dingtalk_node_id,
                DingtalkSyncedNode.is_active == True,  # noqa: E712
            )
            .first()
        )
        if not node:
            raise ValueError("DingTalk document is not synced for the current user")
        return node

    @classmethod
    def _is_supported_adoc(cls, info: dict[str, Any], node: DingtalkSyncedNode) -> bool:
        content_type = str(
            cls._first_value(info, "contentType", "content_type", "type")
            or node.content_type
            or ""
        ).upper()
        extension = str(
            cls._first_value(info, "extension", "fileExtension", "file_extension") or ""
        ).lower()

        if content_type and content_type not in {"ALIDOC", "ADOC"}:
            return False
        if extension and extension != "adoc":
            return False
        return content_type in {"ALIDOC", "ADOC"} or extension == "adoc"

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
    def _markdown_filename(name: str) -> str:
        safe = FILENAME_UNSAFE_RE.sub("_", name).strip().strip(".")
        if not safe:
            safe = "dingtalk-document"
        if not safe.lower().endswith(".md"):
            safe = f"{safe}.md"
        return safe[:180]

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
