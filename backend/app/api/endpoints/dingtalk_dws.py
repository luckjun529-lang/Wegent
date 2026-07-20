# SPDX-FileCopyrightText: 2026 Weibo, Inc.
#
# SPDX-License-Identifier: Apache-2.0

"""DingTalk DWS auth endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from app.core.security import get_current_user
from app.models.user import User
from app.schemas.dingtalk_dws import (
    DwsAuthStatusResponse,
    DwsDeviceLoginResponse,
)
from app.services.dingtalk_dws_service import DwsCommandError, dingtalk_dws_service

router = APIRouter()

logger = logging.getLogger(__name__)


@router.get("/auth/status", response_model=DwsAuthStatusResponse)
async def get_dws_auth_status(
    current_user: User = Depends(get_current_user),
) -> DwsAuthStatusResponse:
    """Return DingTalk DWS auth status for the current user."""
    status = await dingtalk_dws_service.auth_status(current_user.id)
    return DwsAuthStatusResponse(**status)


@router.post("/auth/device-login", response_model=DwsDeviceLoginResponse)
async def start_dws_device_login(
    current_user: User = Depends(get_current_user),
) -> DwsDeviceLoginResponse:
    """Start a device-login flow for a headless backend container."""
    try:
        result = await dingtalk_dws_service.start_device_login(current_user.id)
        return DwsDeviceLoginResponse(**result)
    except DwsCommandError as exc:
        logger.warning("Failed to start DWS device login: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get(
    "/auth/device-login/{session_id}",
    response_model=DwsDeviceLoginResponse,
)
async def get_dws_device_login_status(
    session_id: str,
    current_user: User = Depends(get_current_user),
) -> DwsDeviceLoginResponse:
    """Poll a device-login session started by the current user."""
    try:
        result = await dingtalk_dws_service.get_device_login_status(
            current_user.id,
            session_id,
        )
        return DwsDeviceLoginResponse(**result)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
