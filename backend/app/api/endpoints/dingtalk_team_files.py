# SPDX-FileCopyrightText: 2026 Weibo, Inc.
#
# SPDX-License-Identifier: Apache-2.0

"""DingTalk team-file sync API endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.dependencies import get_db
from app.core.security import get_current_user
from app.models.user import User
from app.schemas.dingtalk_doc import (
    DingtalkDocNode,
    DingtalkDocTreeResponse,
    DingtalkSyncResult,
    DingtalkSyncStatus,
    build_dingtalk_tree,
)
from app.services.dingtalk_team_files_service import DingTalkTeamFilesService

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("", response_model=DingtalkDocTreeResponse)
def get_team_files(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DingtalkDocTreeResponse:
    """Return the current user's synced DingTalk team-file tree."""
    nodes = DingTalkTeamFilesService.get_team_files(current_user.id, db)
    node_schemas = [DingtalkDocNode.model_validate(node) for node in nodes]
    return DingtalkDocTreeResponse(
        nodes=build_dingtalk_tree(node_schemas),
        total_count=len(node_schemas),
    )


@router.post("/sync", response_model=DingtalkSyncResult)
async def sync_team_files(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DingtalkSyncResult:
    """Synchronize all DingTalk team spaces visible to the current user."""
    try:
        result = await DingTalkTeamFilesService.sync_team_files(current_user, db)
        return DingtalkSyncResult(**result)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to sync DingTalk team files: %s", exc)
        raise HTTPException(
            status_code=500,
            detail="Failed to sync DingTalk team files",
        ) from exc


@router.get("/sync-status", response_model=DingtalkSyncStatus)
async def get_team_files_sync_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DingtalkSyncStatus:
    """Return the current user's DingTalk team-file sync status."""
    return DingtalkSyncStatus(
        **await DingTalkTeamFilesService.get_sync_status(current_user, db)
    )
