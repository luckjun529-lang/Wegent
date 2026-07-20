# SPDX-FileCopyrightText: 2026 Weibo, Inc.
#
# SPDX-License-Identifier: Apache-2.0

"""API tests for DingTalk DWS auth endpoints."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.endpoints.dingtalk_dws import router
from app.core import security
from app.models.user import User
from app.services.dingtalk_dws_service import DwsCommandError


def _client(test_user: User) -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix="/dingtalk-dws")
    app.dependency_overrides[security.get_current_user] = lambda: test_user
    return TestClient(app)


class TestDingTalkDwsAuthApi:
    """Tests for DWS auth API endpoints."""

    def test_get_auth_status(self, test_user: User) -> None:
        """GET /auth/status returns normalized DWS auth status."""
        with patch(
            "app.api.endpoints.dingtalk_dws.dingtalk_dws_service.auth_status",
            new=AsyncMock(
                return_value={
                    "is_authenticated": True,
                    "auth_status": "authenticated",
                }
            ),
        ) as mock_status:
            response = _client(test_user).get("/dingtalk-dws/auth/status")

        assert response.status_code == 200
        assert response.json()["is_authenticated"] is True
        assert response.json()["auth_status"] == "authenticated"
        mock_status.assert_awaited_once_with(test_user.id)

    def test_start_device_login(self, test_user: User) -> None:
        """POST /auth/device-login starts and returns a device flow."""
        with patch(
            "app.api.endpoints.dingtalk_dws.dingtalk_dws_service.start_device_login",
            new=AsyncMock(
                return_value={
                    "is_authenticated": False,
                    "auth_status": "pending",
                    "verification_url": (
                        "https://login.dingtalk.com/oauth2/device/"
                        "verify.htm?user_code=ABCD-EFGH"
                    ),
                    "user_code": "ABCD-EFGH",
                    "session_id": "session-1",
                }
            ),
        ) as mock_start:
            response = _client(test_user).post("/dingtalk-dws/auth/device-login")

        assert response.status_code == 200
        data = response.json()
        assert data["auth_status"] == "pending"
        assert data["user_code"] == "ABCD-EFGH"
        assert data["session_id"] == "session-1"
        mock_start.assert_awaited_once_with(test_user.id)

    def test_start_device_login_returns_500_on_dws_error(self, test_user: User) -> None:
        """DWS startup errors surface as a 500 with the command error message."""
        with patch(
            "app.api.endpoints.dingtalk_dws.dingtalk_dws_service.start_device_login",
            new=AsyncMock(side_effect=DwsCommandError("DWS binary not found: dws")),
        ):
            response = _client(test_user).post("/dingtalk-dws/auth/device-login")

        assert response.status_code == 500
        assert "DWS binary not found" in response.json()["detail"]

    def test_poll_device_login(self, test_user: User) -> None:
        """GET /auth/device-login/{session_id} polls a device flow."""
        with patch(
            "app.api.endpoints.dingtalk_dws."
            "dingtalk_dws_service.get_device_login_status",
            new=AsyncMock(
                return_value={
                    "is_authenticated": True,
                    "auth_status": "authenticated",
                    "verification_url": "https://login.dingtalk.com/oauth2/device/"
                    "verify.htm?user_code=ABCD-EFGH",
                    "user_code": "ABCD-EFGH",
                    "session_id": "session-1",
                }
            ),
        ) as mock_poll:
            response = _client(test_user).get(
                "/dingtalk-dws/auth/device-login/session-1"
            )

        assert response.status_code == 200
        assert response.json()["auth_status"] == "authenticated"
        mock_poll.assert_awaited_once_with(test_user.id, "session-1")

    def test_poll_device_login_returns_404_for_missing_session(
        self,
        test_user: User,
    ) -> None:
        """Unknown device-flow sessions return 404."""
        with patch(
            "app.api.endpoints.dingtalk_dws."
            "dingtalk_dws_service.get_device_login_status",
            new=AsyncMock(side_effect=ValueError("Device login session not found")),
        ):
            response = _client(test_user).get("/dingtalk-dws/auth/device-login/missing")

        assert response.status_code == 404
        assert "not found" in response.json()["detail"]
