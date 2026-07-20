# SPDX-FileCopyrightText: 2026 Weibo, Inc.
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for DingTalk DWS CLI service helpers."""

from __future__ import annotations

import stat
from unittest.mock import AsyncMock, patch

import pytest

from app.services.dingtalk_dws_service import (
    DingTalkDwsService,
    DwsCommandError,
    DwsCommandResult,
)


class TestDingTalkDwsService:
    """Tests for DWS runner and auth helpers."""

    def test_with_json_format_appends_format_json(self) -> None:
        """All DWS commands are forced into JSON output unless already specified."""
        assert DingTalkDwsService._with_json_format(["auth", "status"]) == [
            "auth",
            "status",
            "--format",
            "json",
        ]
        assert DingTalkDwsService._with_json_format(
            ["auth", "status", "--format", "json"]
        ) == ["auth", "status", "--format", "json"]

    def test_build_env_uses_per_user_config_dir(self, tmp_path, monkeypatch) -> None:
        """DWS_CONFIG_DIR is isolated per Wegent user with 0700 permissions."""
        monkeypatch.setattr(
            "app.services.dingtalk_dws_service.settings.DWS_CONFIG_ROOT",
            str(tmp_path),
        )

        env = DingTalkDwsService._build_env(user_id=42)

        config_dir = tmp_path / "users" / "42"
        assert env["DWS_CONFIG_DIR"] == str(config_dir)
        assert stat.S_IMODE(config_dir.stat().st_mode) == 0o700

    @pytest.mark.asyncio
    async def test_auth_status_authenticated(self) -> None:
        """Authenticated DWS payloads are normalized for API callers."""
        with patch.object(
            DingTalkDwsService,
            "run",
            new=AsyncMock(
                return_value=DwsCommandResult(
                    data={"authenticated": True},
                    stdout='{"authenticated":true}',
                    stderr="",
                    returncode=0,
                )
            ),
        ) as mock_run:
            status = await DingTalkDwsService.auth_status(user_id=42)

        assert status == {
            "is_authenticated": True,
            "auth_status": "authenticated",
        }
        mock_run.assert_awaited_once_with(42, ["auth", "status"], timeout=20)

    @pytest.mark.asyncio
    async def test_auth_status_unauthenticated_from_error_output(self) -> None:
        """DWS login errors with 未登录 are treated as unauthenticated."""
        with patch.object(
            DingTalkDwsService,
            "run",
            new=AsyncMock(
                side_effect=DwsCommandError(
                    "not logged in",
                    stdout='{"message":"未登录"}',
                )
            ),
        ):
            status = await DingTalkDwsService.auth_status(user_id=42)

        assert status == {
            "is_authenticated": False,
            "auth_status": "unauthenticated",
        }

    def test_extract_device_login_payload_from_json(self) -> None:
        """Device-login payloads can be parsed from JSON output."""
        payload = DingTalkDwsService._extract_device_login_payload(
            '{"verification_url":"https://login.dingtalk.com/oauth2/device/'
            'verify.htm?user_code=GLLM-GXKH","user_code":"GLLM-GXKH"}'
        )

        assert payload == {
            "verification_url": (
                "https://login.dingtalk.com/oauth2/device/"
                "verify.htm?user_code=GLLM-GXKH"
            ),
            "user_code": "GLLM-GXKH",
        }

    def test_extract_device_login_payload_from_human_output(self) -> None:
        """Device-login payloads can be parsed from human-readable DWS output."""
        payload = DingTalkDwsService._extract_device_login_payload(
            "Open https://login.dingtalk.com/oauth2/device/"
            "verify.htm?user_code=ABCD-EFGH to continue"
        )

        assert payload == {
            "verification_url": (
                "https://login.dingtalk.com/oauth2/device/"
                "verify.htm?user_code=ABCD-EFGH"
            ),
            "user_code": "ABCD-EFGH",
        }
