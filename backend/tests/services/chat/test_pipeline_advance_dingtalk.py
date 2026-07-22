# SPDX-FileCopyrightText: 2026 Weibo, Inc.
#
# SPDX-License-Identifier: Apache-2.0

"""DingTalk attachment compensation tests for pipeline chat sends."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.chat.pipeline_advance import advance_pipeline_stage_and_send


@pytest.mark.asyncio
async def test_pipeline_rag_failure_cleans_materialized_dingtalk_attachments() -> None:
    """A failed handoff must not retain attachments that were never linked."""
    db = MagicMock()
    user = MagicMock(id=42)
    team = MagicMock()
    payload = SimpleNamespace()
    advance_result = {
        "success": True,
        "is_pipeline_complete": False,
        "next_stage_bot_id": 8,
        "current_stage_bot_id": 7,
        "handoff_message": "Continue",
        "context_passing": None,
    }

    with (
        patch(
            "app.services.chat.pipeline_advance.pipeline_stage_service.pipeline_confirm",
            return_value=advance_result,
        ),
        patch(
            "app.services.chat.pipeline_advance._prepare_payload_contexts",
            new=AsyncMock(return_value=([901], None, [901])),
        ),
        patch(
            "app.services.chat.pipeline_advance.process_context_and_rag",
            new=AsyncMock(side_effect=RuntimeError("RAG unavailable")),
        ),
        patch(
            "app.services.chat.pipeline_advance._cleanup_dingtalk_attachments"
        ) as cleanup,
    ):
        with pytest.raises(RuntimeError, match="RAG unavailable"):
            await advance_pipeline_stage_and_send(
                db=db,
                user=user,
                team=team,
                task_id=12,
                message="Continue",
                payload=payload,
                skip_sid=None,
                auth_token="",
            )

    cleanup.assert_called_once_with(db, 42, [901])
