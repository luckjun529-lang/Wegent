# SPDX-FileCopyrightText: 2026 Weibo, Inc.
#
# SPDX-License-Identifier: Apache-2.0

"""Administrator endpoints for trusted plugin repositories."""

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_db
from app.core.security import get_admin_user
from app.models.user import User
from app.schemas.plugin_repository import (
    PluginRepositoryCreateRequest,
    PluginRepositoryItem,
    PluginRepositoryListResponse,
    PluginRepositoryMembersResponse,
    PluginRepositoryMembersUpdateRequest,
    PluginRepositoryUpdateRequest,
)
from app.services.plugin_repository_service import plugin_repository_service

router = APIRouter(prefix="/plugin-repositories")


@router.get("", response_model=PluginRepositoryListResponse)
def list_repositories(
    db: Session = Depends(get_db), current_user: User = Depends(get_admin_user)
) -> PluginRepositoryListResponse:
    return PluginRepositoryListResponse(
        items=plugin_repository_service.list_repositories(db, current_user, admin=True)
    )


@router.post(
    "", response_model=PluginRepositoryItem, status_code=status.HTTP_201_CREATED
)
def create_repository(
    request: PluginRepositoryCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_admin_user),
) -> PluginRepositoryItem:
    return plugin_repository_service.create_repository(db, request, current_user.id)


@router.patch("/{repository_id}", response_model=PluginRepositoryItem)
def update_repository(
    repository_id: int,
    request: PluginRepositoryUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_admin_user),
) -> PluginRepositoryItem:
    del current_user
    return plugin_repository_service.update_repository(db, repository_id, request)


@router.post("/{repository_id}/validate", response_model=PluginRepositoryItem)
def validate_repository(
    repository_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_admin_user),
) -> PluginRepositoryItem:
    del current_user
    return plugin_repository_service.validate_repository(db, repository_id)


@router.get("/{repository_id}/members", response_model=PluginRepositoryMembersResponse)
def list_members(
    repository_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_admin_user),
) -> PluginRepositoryMembersResponse:
    del current_user
    return plugin_repository_service.list_members(db, repository_id)


@router.put("/{repository_id}/members", response_model=PluginRepositoryMembersResponse)
def replace_members(
    repository_id: int,
    request: PluginRepositoryMembersUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_admin_user),
) -> PluginRepositoryMembersResponse:
    return plugin_repository_service.replace_members(
        db, repository_id, request.items, current_user.id
    )
