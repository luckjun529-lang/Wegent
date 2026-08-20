# SPDX-FileCopyrightText: 2026 Weibo, Inc.
#
# SPDX-License-Identifier: Apache-2.0

"""Managed Git repository publication control plane."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from fastapi import HTTPException
from packaging.version import InvalidVersion, Version
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.namespace import Namespace
from app.models.plugin_marketplace import (
    EPOCH_TIME,
    Plugin,
    PluginRelease,
    PluginRepository,
    PluginRepositoryPublication,
    unset_datetime,
    unset_id,
    unset_str,
)
from app.models.resource_member import MemberStatus, ResourceMember
from app.models.share_link import ResourceType
from app.models.user import User
from app.schemas.base_role import BaseRole, get_highest_role, has_permission
from app.schemas.plugin_repository import (
    PluginRepositoryCandidateItem,
    PluginRepositoryCreateRequest,
    PluginRepositoryInspectResponse,
    PluginRepositoryItem,
    PluginRepositoryMemberItem,
    PluginRepositoryMemberRequest,
    PluginRepositoryMembersResponse,
    PluginRepositoryPublicationItem,
    PluginRepositoryRefItem,
    PluginRepositoryUpdateRequest,
)
from app.services.external_entity_resolver import resolve_entity_roles_for_resource
from app.services.official_plugin_publisher import official_plugin_publisher
from app.services.plugin_repository_git import (
    PluginRepositoryGitError,
    PluginRepositoryGitProvider,
    RepositoryPluginCandidate,
    create_plugin_repository_provider,
    normalize_repository_path,
    validate_repository_url,
)
from shared.utils.crypto import decrypt_git_token, encrypt_git_token

SHA_PATTERN = re.compile(r"^[0-9a-f]{40,64}$")


class PluginRepositoryService:
    """Configure repositories and publish immutable releases from pinned commits."""

    def create_repository(
        self, db: Session, request: PluginRepositoryCreateRequest, user_id: int
    ) -> PluginRepositoryItem:
        self._validate_configuration(request)
        repository = PluginRepository(
            name=request.name.strip(),
            provider=request.provider,
            repository_url=request.repositoryUrl.strip().rstrip("/"),
            visibility=request.visibility,
            default_ref=request.defaultRef.strip(),
            marketplace_path=request.marketplacePath.strip(),
            allowed_branch_patterns_json=request.allowedBranchPatterns,
            allowed_tag_patterns_json=request.allowedTagPatterns,
            credential_encrypted=self._encrypt(request.credential),
            is_internal=request.isInternal,
            is_enabled=request.isEnabled,
            created_by_user_id=user_id,
        )
        validate_repository_url(repository)
        db.add(repository)
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise HTTPException(
                status_code=409, detail="Plugin repository URL already exists"
            ) from exc
        db.refresh(repository)
        return self.repository_item(repository)

    def update_repository(
        self, db: Session, repository_id: int, request: PluginRepositoryUpdateRequest
    ) -> PluginRepositoryItem:
        repository = self.get_repository(db, repository_id)
        values = request.model_dump(exclude_unset=True)
        mapping = {
            "repositoryUrl": "repository_url",
            "defaultRef": "default_ref",
            "marketplacePath": "marketplace_path",
            "allowedBranchPatterns": "allowed_branch_patterns_json",
            "allowedTagPatterns": "allowed_tag_patterns_json",
            "isInternal": "is_internal",
            "isEnabled": "is_enabled",
        }
        for key, value in values.items():
            if key in {"credential", "credentialAction"}:
                continue
            setattr(
                repository,
                mapping.get(key, key),
                value.strip() if isinstance(value, str) else value,
            )
        if request.credentialAction == "replace":
            repository.credential_encrypted = self._encrypt(request.credential)
        elif request.credentialAction == "remove":
            repository.credential_encrypted = ""
        self._validate_configuration(repository)
        validate_repository_url(repository)
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise HTTPException(
                status_code=409, detail="Plugin repository URL already exists"
            ) from exc
        db.refresh(repository)
        return self.repository_item(repository)

    def list_repositories(
        self, db: Session, user: User, *, admin: bool = False
    ) -> list[PluginRepositoryItem]:
        repositories = db.query(PluginRepository).order_by(PluginRepository.name).all()
        items: list[PluginRepositoryItem] = []
        for repository in repositories:
            role = (
                BaseRole.Owner.value
                if admin
                else self.user_role(db, repository.id, user.id)
            )
            if admin or (repository.is_enabled and role):
                items.append(self.repository_item(repository, role))
        return items

    def validate_repository(
        self, db: Session, repository_id: int
    ) -> PluginRepositoryItem:
        repository = self.get_repository(db, repository_id)
        try:
            provider = self.provider(repository)
            resolved = provider.resolve_ref(repository.default_ref, "branch")
            provider.inspect_plugins(resolved.commit_sha)
        except Exception as exc:
            repository.last_error = self._safe_error(exc)
            db.commit()
            raise self._http_error(exc) from exc
        repository.last_validated_at = datetime.now()
        repository.last_error = ""
        db.commit()
        db.refresh(repository)
        return self.repository_item(repository)

    def list_members(
        self, db: Session, repository_id: int
    ) -> PluginRepositoryMembersResponse:
        self.get_repository(db, repository_id)
        rows = (
            db.query(ResourceMember)
            .filter(
                ResourceMember.resource_type == ResourceType.PLUGIN_REPOSITORY.value,
                ResourceMember.resource_id == repository_id,
                ResourceMember.status == MemberStatus.APPROVED.value,
            )
            .order_by(ResourceMember.id)
            .all()
        )
        return PluginRepositoryMembersResponse(
            items=[
                PluginRepositoryMemberItem(
                    id=row.id,
                    entityType=row.entity_type,
                    entityId=row.entity_id,
                    displayName=row.entity_display_name,
                    role=row.role,
                )
                for row in rows
            ]
        )

    def replace_members(
        self,
        db: Session,
        repository_id: int,
        members: list[PluginRepositoryMemberRequest],
        admin_user_id: int,
    ) -> PluginRepositoryMembersResponse:
        self.get_repository(db, repository_id)
        seen: set[tuple[str, str]] = set()
        for member in members:
            key = (member.entityType, member.entityId)
            if key in seen:
                raise HTTPException(
                    status_code=422, detail="Duplicate repository member"
                )
            seen.add(key)
            self._validate_member(db, member)
        db.query(ResourceMember).filter(
            ResourceMember.resource_type == ResourceType.PLUGIN_REPOSITORY.value,
            ResourceMember.resource_id == repository_id,
        ).delete(synchronize_session=False)
        for member in members:
            db.add(
                ResourceMember(
                    resource_type=ResourceType.PLUGIN_REPOSITORY.value,
                    resource_id=repository_id,
                    entity_type=member.entityType,
                    entity_id=member.entityId,
                    entity_display_name=member.displayName,
                    user_id=int(member.entityId) if member.entityType == "user" else 0,
                    role=member.role,
                    status=MemberStatus.APPROVED.value,
                    invited_by_user_id=admin_user_id,
                    reviewed_by_user_id=admin_user_id,
                    reviewed_at=datetime.now(),
                )
            )
        db.commit()
        return self.list_members(db, repository_id)

    def list_refs(
        self, db: Session, repository_id: int, user: User
    ) -> list[PluginRepositoryRefItem]:
        repository = self.authorized_repository(
            db, repository_id, user, BaseRole.Reporter
        )
        try:
            refs = self.provider(repository).list_refs()
        except Exception as exc:
            raise self._http_error(exc) from exc
        return [
            PluginRepositoryRefItem(
                name=ref.name, kind=ref.kind, commitSha=ref.commit_sha
            )
            for ref in refs
        ]

    def inspect(
        self,
        db: Session,
        repository_id: int,
        user: User,
        ref: str,
        kind: Literal["branch", "tag"],
    ) -> PluginRepositoryInspectResponse:
        repository = self.authorized_repository(
            db, repository_id, user, BaseRole.Reporter
        )
        try:
            provider = self.provider(repository)
            resolved = provider.resolve_ref(ref, kind)
            candidates = provider.inspect_plugins(resolved.commit_sha)
        except Exception as exc:
            raise self._http_error(exc) from exc
        return PluginRepositoryInspectResponse(
            repositoryId=repository.id,
            requestedRef=ref,
            refKind=kind,
            resolvedCommitSha=resolved.commit_sha,
            candidates=[
                self._candidate_item(db, repository, candidate)
                for candidate in candidates
            ],
        )

    def create_publication(
        self,
        db: Session,
        repository_id: int,
        user: User,
        slug: str,
        ref: str,
        kind: Literal["branch", "tag"],
        expected_sha: str,
    ) -> PluginRepositoryPublicationItem:
        if not settings.PLUGIN_REPOSITORY_PUBLISH_ENABLED:
            raise HTTPException(
                status_code=503, detail="Plugin repository publishing is disabled"
            )
        repository = self.authorized_repository(
            db, repository_id, user, BaseRole.Developer
        )
        if not SHA_PATTERN.fullmatch(expected_sha.lower()):
            raise HTTPException(status_code=422, detail="Invalid expected commit SHA")
        try:
            provider = self.provider(repository)
            resolved = provider.resolve_ref(ref, kind)
            if resolved.commit_sha.lower() != expected_sha.lower():
                raise HTTPException(
                    status_code=409, detail="Git ref moved; inspect the ref again"
                )
            candidates = provider.inspect_plugins(resolved.commit_sha)
        except HTTPException:
            raise
        except Exception as exc:
            raise self._http_error(exc) from exc
        candidate = next((item for item in candidates if item.slug == slug), None)
        if candidate is None:
            raise HTTPException(
                status_code=422,
                detail="Plugin is not present in the marketplace manifest",
            )
        item = self._candidate_item(db, repository, candidate)
        if not item.publishable:
            raise HTTPException(status_code=409, detail=item.blockedReason)
        publication = PluginRepositoryPublication(
            repository_id=repository.id,
            plugin_slug=slug,
            requested_ref=ref,
            ref_kind=kind,
            commit_sha=resolved.commit_sha.lower(),
            version=candidate.version,
            status="queued",
            requested_by_user_id=user.id,
        )
        db.add(publication)
        db.commit()
        db.refresh(publication)
        from app.tasks.plugin_marketplace_tasks import publish_plugin_repository_release

        try:
            publish_plugin_repository_release.delay(publication.id)
        except Exception as exc:
            self._fail(
                db, publication, "queue_dispatch_failed", "Queue dispatch failed"
            )
            raise HTTPException(
                status_code=503, detail="Plugin publication queue is unavailable"
            ) from exc
        return self.publication_item(publication)

    def list_publications(
        self, db: Session, repository_id: int, user: User
    ) -> list[PluginRepositoryPublicationItem]:
        self.authorized_repository(db, repository_id, user, BaseRole.Reporter)
        rows = (
            db.query(PluginRepositoryPublication)
            .filter(PluginRepositoryPublication.repository_id == repository_id)
            .order_by(PluginRepositoryPublication.created_at.desc())
            .limit(100)
            .all()
        )
        return [self.publication_item(row) for row in rows]

    def get_publication(
        self, db: Session, publication_id: int, user: User
    ) -> PluginRepositoryPublicationItem:
        publication = db.get(PluginRepositoryPublication, publication_id)
        if not publication:
            raise HTTPException(status_code=404, detail="Publication not found")
        self.authorized_repository(
            db, publication.repository_id, user, BaseRole.Reporter
        )
        return self.publication_item(publication)

    def retry_publication(
        self, db: Session, publication_id: int, user: User
    ) -> PluginRepositoryPublicationItem:
        if not settings.PLUGIN_REPOSITORY_PUBLISH_ENABLED:
            raise HTTPException(
                status_code=503, detail="Plugin repository publishing is disabled"
            )
        publication = db.get(PluginRepositoryPublication, publication_id)
        if not publication:
            raise HTTPException(status_code=404, detail="Publication not found")
        self.authorized_repository(
            db, publication.repository_id, user, BaseRole.Developer
        )
        if publication.status != "failed":
            raise HTTPException(
                status_code=409, detail="Only failed publications can be retried"
            )
        publication.status = "queued"
        publication.error_code = ""
        publication.error_message = ""
        publication.started_at = EPOCH_TIME
        publication.finished_at = EPOCH_TIME
        db.commit()
        from app.tasks.plugin_marketplace_tasks import (
            publish_plugin_repository_release,
        )

        try:
            publish_plugin_repository_release.delay(publication.id)
        except Exception as exc:
            self._fail(
                db, publication, "queue_dispatch_failed", "Queue dispatch failed"
            )
            raise HTTPException(
                status_code=503, detail="Plugin publication queue is unavailable"
            ) from exc
        db.refresh(publication)
        return self.publication_item(publication)

    def process_publication(self, db: Session, publication_id: int) -> None:
        publication = db.get(PluginRepositoryPublication, publication_id)
        if not publication or publication.status not in {"queued", "failed"}:
            return
        if not settings.PLUGIN_REPOSITORY_PUBLISH_ENABLED:
            self._fail(
                db,
                publication,
                "publishing_disabled",
                "Plugin repository publishing is disabled",
            )
            return
        repository = db.get(PluginRepository, publication.repository_id)
        if not repository or not repository.is_enabled:
            self._fail(
                db, publication, "repository_unavailable", "Repository is unavailable"
            )
            return
        publication.status = "fetching"
        publication.started_at = datetime.now()
        publication.error_code = ""
        publication.error_message = ""
        db.commit()
        try:
            provider = self.provider(repository)
            resolved = provider.resolve_ref(
                publication.requested_ref, publication.ref_kind
            )
            if resolved.commit_sha.lower() != publication.commit_sha.lower():
                raise PluginRepositoryGitError(
                    "Git ref moved after publication was queued"
                )
            candidates = provider.inspect_plugins(publication.commit_sha)
            candidate = next(
                (item for item in candidates if item.slug == publication.plugin_slug),
                None,
            )
            if candidate is None or candidate.version != publication.version:
                raise PluginRepositoryGitError(
                    "Pinned marketplace metadata changed unexpectedly"
                )
            publication.status = "validating"
            db.commit()
            files = provider.read_plugin_files(publication.commit_sha, candidate.path)
            built = official_plugin_publisher.build_package_from_files(files)
            if built.name != candidate.slug or built.version != candidate.version:
                raise PluginRepositoryGitError(
                    "Packaged manifest does not match inspected metadata"
                )
            publication.status = "publishing"
            publication.package_sha256 = built.sha256
            db.commit()
            result = official_plugin_publisher.publish_package(
                db,
                built=built,
                slug=candidate.slug,
                listing_type=candidate.listing_type,
                visibility=repository.visibility,
                created_by_user_id=publication.requested_by_user_id,
                source_repository_id=repository.id,
                provenance={
                    "kind": "plugin_repository",
                    "repositoryId": repository.id,
                    "repositoryUrl": repository.repository_url,
                    "ref": publication.requested_ref,
                    "refKind": publication.ref_kind,
                    "commitSha": publication.commit_sha,
                    "operatorUserId": publication.requested_by_user_id,
                },
            )
            db.refresh(publication)
            release = result.release
            publication.plugin_id = release.plugin_id
            publication.release_id = release.id
            publication.status = "published"
            publication.finished_at = datetime.now()
            db.commit()
        except Exception as exc:
            db.rollback()
            publication = db.get(PluginRepositoryPublication, publication_id)
            if publication:
                self._fail(db, publication, type(exc).__name__, self._safe_error(exc))
            raise

    def provider(self, repository: PluginRepository) -> PluginRepositoryGitProvider:
        credential = (
            decrypt_git_token(repository.credential_encrypted)
            if repository.credential_encrypted
            else ""
        )
        return create_plugin_repository_provider(repository, credential)

    def authorized_repository(
        self, db: Session, repository_id: int, user: User, required: BaseRole
    ) -> PluginRepository:
        repository = self.get_repository(db, repository_id)
        if not repository.is_enabled:
            raise HTTPException(status_code=404, detail="Plugin repository not found")
        role = (
            BaseRole.Owner.value
            if user.role == "admin"
            else self.user_role(db, repository_id, user.id)
        )
        if not role or not has_permission(role, required):
            raise HTTPException(
                status_code=403, detail="Plugin repository permission denied"
            )
        return repository

    def user_role(self, db: Session, repository_id: int, user_id: int) -> str | None:
        rows = (
            db.query(ResourceMember.role)
            .filter(
                ResourceMember.resource_type == ResourceType.PLUGIN_REPOSITORY.value,
                ResourceMember.resource_id == repository_id,
                ResourceMember.entity_type == "user",
                ResourceMember.entity_id == str(user_id),
                ResourceMember.status == MemberStatus.APPROVED.value,
            )
            .all()
        )
        roles = [row[0] for row in rows]
        roles.extend(
            resolve_entity_roles_for_resource(
                db,
                ResourceType.PLUGIN_REPOSITORY.value,
                repository_id,
                user_id,
            )
        )
        return get_highest_role(roles)

    def get_repository(self, db: Session, repository_id: int) -> PluginRepository:
        repository = db.get(PluginRepository, repository_id)
        if not repository:
            raise HTTPException(status_code=404, detail="Plugin repository not found")
        return repository

    def repository_item(
        self, repository: PluginRepository, my_role: str | None = None
    ) -> PluginRepositoryItem:
        return PluginRepositoryItem(
            id=repository.id,
            name=repository.name,
            provider=repository.provider,
            repositoryUrl=repository.repository_url,
            visibility=repository.visibility,
            defaultRef=repository.default_ref,
            marketplacePath=repository.marketplace_path,
            allowedBranchPatterns=list(repository.allowed_branch_patterns_json or []),
            allowedTagPatterns=list(repository.allowed_tag_patterns_json or []),
            hasCredential=bool(repository.credential_encrypted),
            isInternal=repository.is_internal,
            isEnabled=repository.is_enabled,
            myRole=my_role,
            lastValidatedAt=unset_datetime(repository.last_validated_at),
            lastError=unset_str(repository.last_error),
            createdAt=repository.created_at,
            updatedAt=repository.updated_at,
        )

    def publication_item(
        self, publication: PluginRepositoryPublication
    ) -> PluginRepositoryPublicationItem:
        return PluginRepositoryPublicationItem(
            id=publication.id,
            repositoryId=publication.repository_id,
            pluginSlug=publication.plugin_slug,
            requestedRef=publication.requested_ref,
            refKind=publication.ref_kind,
            commitSha=publication.commit_sha,
            version=unset_str(publication.version),
            status=publication.status,
            requestedByUserId=publication.requested_by_user_id,
            pluginId=unset_id(publication.plugin_id),
            releaseId=unset_id(publication.release_id),
            packageSha256=unset_str(publication.package_sha256),
            errorCode=unset_str(publication.error_code),
            errorMessage=unset_str(publication.error_message),
            createdAt=publication.created_at,
            startedAt=unset_datetime(publication.started_at),
            finishedAt=unset_datetime(publication.finished_at),
            updatedAt=publication.updated_at,
        )

    def _candidate_item(
        self,
        db: Session,
        repository: PluginRepository,
        candidate: RepositoryPluginCandidate,
    ) -> PluginRepositoryCandidateItem:
        blocked = ""
        current_version = None
        plugin = db.query(Plugin).filter(Plugin.slug == candidate.slug).first()
        if plugin:
            if plugin.source_repository_id not in {None, repository.id}:
                blocked = "Plugin slug is bound to a different source repository"
            elif (
                plugin.source_type != "native"
                or plugin.source_provider != "wework"
                or plugin.owner_user_id != 0
            ):
                blocked = "Plugin slug is owned by a different publisher"
            elif plugin.latest_release_id:
                release = db.get(PluginRelease, plugin.latest_release_id)
                current_version = release.version if release else None
        try:
            version = Version(candidate.version)
            if current_version and version < Version(current_version):
                blocked = "Plugin version is older than the current market version"
        except InvalidVersion:
            blocked = "Plugin version must be SemVer"
        return PluginRepositoryCandidateItem(
            slug=candidate.slug,
            displayName=candidate.display_name,
            version=candidate.version,
            path=candidate.path,
            listingType=candidate.listing_type,
            currentVersion=current_version,
            sourceRepositoryId=(
                unset_id(plugin.source_repository_id) if plugin else None
            ),
            publishable=not blocked,
            blockedReason=blocked or None,
        )

    def _validate_configuration(
        self, repository: PluginRepository | PluginRepositoryCreateRequest
    ) -> None:
        provider = repository.provider
        visibility = repository.visibility
        if isinstance(repository, PluginRepository):
            internal = repository.is_internal
            marketplace_path = repository.marketplace_path
            branch_patterns = repository.allowed_branch_patterns_json
            tag_patterns = repository.allowed_tag_patterns_json
        else:
            internal = repository.isInternal
            marketplace_path = repository.marketplacePath
            branch_patterns = repository.allowedBranchPatterns
            tag_patterns = repository.allowedTagPatterns
        normalize_repository_path(marketplace_path, field="marketplacePath")
        if not branch_patterns and not tag_patterns:
            raise HTTPException(
                status_code=422, detail="At least one allowed ref pattern is required"
            )
        if provider == "github" and (internal or visibility != "public"):
            raise HTTPException(
                status_code=422, detail="GitHub repositories must be public sources"
            )
        if provider == "gitlab" and (not internal or visibility != "workspace"):
            raise HTTPException(
                status_code=422,
                detail="GitLab repositories must be internal workspace sources",
            )

    def _validate_member(
        self, db: Session, member: PluginRepositoryMemberRequest
    ) -> None:
        if member.entityType == "user":
            if not member.entityId.isdigit() or not db.get(User, int(member.entityId)):
                raise HTTPException(
                    status_code=422, detail="Repository member user does not exist"
                )
        elif not member.entityId.isdigit() or not db.get(
            Namespace, int(member.entityId)
        ):
            raise HTTPException(
                status_code=422, detail="Repository member namespace does not exist"
            )

    def _encrypt(self, credential: str | None) -> str:
        value = (credential or "").strip()
        return encrypt_git_token(value) if value else ""

    def _safe_error(self, exc: Exception) -> str:
        if isinstance(exc, HTTPException):
            return str(exc.detail)[:1000]
        return str(exc)[:1000] or type(exc).__name__

    def _http_error(self, exc: Exception) -> HTTPException:
        if isinstance(exc, HTTPException):
            return exc
        status = 422 if isinstance(exc, (PluginRepositoryGitError, ValueError)) else 502
        return HTTPException(status_code=status, detail=self._safe_error(exc))

    def _fail(
        self,
        db: Session,
        publication: PluginRepositoryPublication,
        code: str,
        message: str,
    ) -> None:
        publication.status = "failed"
        publication.error_code = code[:100]
        publication.error_message = message[:1000]
        publication.finished_at = datetime.now()
        db.commit()


plugin_repository_service = PluginRepositoryService()
