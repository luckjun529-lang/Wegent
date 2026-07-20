# SPDX-FileCopyrightText: 2026 Weibo, Inc.
#
# SPDX-License-Identifier: Apache-2.0

"""Schemas for DingTalk DWS auth endpoints."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel

DwsAuthStatus = Literal[
    "authenticated",
    "unauthenticated",
    "pending",
    "error",
    "timeout",
    "cancelled",
]


class DwsAuthStatusResponse(BaseModel):
    """Current DWS auth state for the logged-in Wegent user."""

    is_authenticated: bool = False
    auth_status: DwsAuthStatus = "unauthenticated"
    error: Optional[str] = None


class DwsDeviceLoginResponse(DwsAuthStatusResponse):
    """DWS device-login state returned from start/poll endpoints."""

    verification_url: Optional[str] = None
    user_code: Optional[str] = None
    session_id: Optional[str] = None
