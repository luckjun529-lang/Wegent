# SPDX-FileCopyrightText: 2026 Weibo, Inc.
#
# SPDX-License-Identifier: Apache-2.0

"""Developer endpoints for inspecting and publishing managed Git sources."""

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_db
from app.core.security import get_current_user
from app.models.user import User
from app.schemas.plugin_repository import (
    PluginRepositoryInspectRequest,
    PluginRepositoryInspectResponse,
    PluginRepositoryListResponse,
    PluginRepositoryPublicationItem,
    PluginRepositoryPublicationListResponse,
    PluginRepositoryPublishRequest,
    PluginRepositoryRefsResponse,
)
from app.services.plugin_repository_service import plugin_repository_service

router = APIRouter(prefix="/developer/plugins")


@router.get("/repositories", response_model=PluginRepositoryListResponse)
def list_repositories(
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
) -> PluginRepositoryListResponse:
    return PluginRepositoryListResponse(
        items=plugin_repository_service.list_repositories(db, current_user)
    )


@router.get(
    "/repositories/{repository_id}/refs", response_model=PluginRepositoryRefsResponse
)
def list_refs(
    repository_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PluginRepositoryRefsResponse:
    return PluginRepositoryRefsResponse(
        items=plugin_repository_service.list_refs(db, repository_id, current_user)
    )


@router.post(
    "/repositories/{repository_id}/inspect",
    response_model=PluginRepositoryInspectResponse,
)
def inspect_repository(
    repository_id: int,
    request: PluginRepositoryInspectRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PluginRepositoryInspectResponse:
    return plugin_repository_service.inspect(
        db, repository_id, current_user, request.ref, request.kind
    )


@router.post(
    "/repositories/{repository_id}/publications",
    response_model=PluginRepositoryPublicationItem,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_publication(
    repository_id: int,
    request: PluginRepositoryPublishRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PluginRepositoryPublicationItem:
    return plugin_repository_service.create_publication(
        db,
        repository_id,
        current_user,
        request.slug,
        request.ref,
        request.kind,
        request.expectedCommitSha,
    )


@router.get(
    "/repositories/{repository_id}/publications",
    response_model=PluginRepositoryPublicationListResponse,
)
def list_publications(
    repository_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PluginRepositoryPublicationListResponse:
    return PluginRepositoryPublicationListResponse(
        items=plugin_repository_service.list_publications(
            db, repository_id, current_user
        )
    )


@router.get(
    "/publications/{publication_id}", response_model=PluginRepositoryPublicationItem
)
def get_publication(
    publication_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PluginRepositoryPublicationItem:
    return plugin_repository_service.get_publication(db, publication_id, current_user)


@router.post(
    "/publications/{publication_id}/retry",
    response_model=PluginRepositoryPublicationItem,
    status_code=status.HTTP_202_ACCEPTED,
)
def retry_publication(
    publication_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PluginRepositoryPublicationItem:
    return plugin_repository_service.retry_publication(
        db, publication_id, current_user
    )
