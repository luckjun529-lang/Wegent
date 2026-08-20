# SPDX-FileCopyrightText: 2026 Weibo, Inc.
#
# SPDX-License-Identifier: Apache-2.0

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.namespace import Namespace
from app.models.plugin_marketplace import (
    Plugin,
    PluginRelease,
    PluginRepository,
    PluginRepositoryPublication,
)
from app.models.resource_member import MemberStatus, ResourceMember
from app.models.share_link import ResourceType
from app.models.user import User
from app.schemas.base_role import BaseRole
from app.services.official_plugin_publisher import OfficialPluginPublisher
from app.services.plugin_repository_git import (
    PluginRepositoryGitError,
    PluginRepositoryGitProvider,
    RepositoryFile,
    RepositoryPluginCandidate,
    RepositoryRef,
    normalize_repository_path,
    validate_repository_url,
)
from app.services.plugin_repository_service import PluginRepositoryService


class FakeProvider:
    def __init__(self, sha: str = "a" * 40) -> None:
        self.sha = sha

    def list_refs(self) -> list[RepositoryRef]:
        return [RepositoryRef(name="main", kind="branch", commit_sha=self.sha)]

    def resolve_ref(self, name: str, kind: str) -> RepositoryRef:
        return RepositoryRef(name=name, kind=kind, commit_sha=self.sha)

    def inspect_plugins(self, _sha: str) -> list[RepositoryPluginCandidate]:
        return [
            RepositoryPluginCandidate(
                slug="example-plugin",
                display_name="Example Plugin",
                version="1.0.0",
                path="plugins/example-plugin",
                listing_type="plugin",
            )
        ]

    def read_plugin_files(self, _sha: str, _path: str) -> dict[str, bytes]:
        return {".codex-plugin/plugin.json": b"{}"}


class UnsafeTreeProvider(PluginRepositoryGitProvider):
    def __init__(self, entries: list[RepositoryFile], content: bytes = b"file") -> None:
        self.entries = entries
        self.content = content

    def list_tree(self, _sha: str, _path: str) -> list[RepositoryFile]:
        return self.entries

    def read_blob(self, _sha: str, _entry: RepositoryFile) -> bytes:
        return self.content


def _repository(test_db: Session) -> PluginRepository:
    repository = PluginRepository(
        name="Public plugins",
        provider="github",
        repository_url="https://github.com/wecode-ai/wework-plugins",
        visibility="public",
        default_ref="main",
        marketplace_path=".agents/plugins/marketplace.json",
        allowed_branch_patterns_json=["main"],
        allowed_tag_patterns_json=["v*"],
        is_internal=False,
        is_enabled=True,
    )
    test_db.add(repository)
    test_db.commit()
    test_db.refresh(repository)
    return repository


def _grant(
    test_db: Session, repository: PluginRepository, user: User, role: str
) -> None:
    test_db.add(
        ResourceMember(
            resource_type=ResourceType.PLUGIN_REPOSITORY.value,
            resource_id=repository.id,
            entity_type="user",
            entity_id=str(user.id),
            user_id=user.id,
            role=role,
            status=MemberStatus.APPROVED.value,
        )
    )
    test_db.commit()


def test_repository_path_rejects_parent_traversal() -> None:
    with pytest.raises(PluginRepositoryGitError, match="safe"):
        normalize_repository_path("plugins/../secret", field="path")


def test_public_repository_rejects_non_https() -> None:
    repository = SimpleNamespace(
        repository_url="http://github.com/wecode-ai/wework-plugins",
        provider="github",
        is_internal=False,
    )
    with pytest.raises(PluginRepositoryGitError, match="HTTPS"):
        validate_repository_url(repository)


def test_repository_package_rejects_symlink() -> None:
    provider = UnsafeTreeProvider(
        [RepositoryFile("plugins/example/link", "120000", "blob")]
    )
    with pytest.raises(PluginRepositoryGitError, match="Symbolic links"):
        provider.read_plugin_files("a" * 40, "plugins/example")


def test_repository_package_rejects_git_lfs_pointer() -> None:
    provider = UnsafeTreeProvider(
        [RepositoryFile("plugins/example/asset.bin", "100644", "blob")],
        b"version https://git-lfs.github.com/spec/v1\n",
    )
    with pytest.raises(PluginRepositoryGitError, match="Git LFS"):
        provider.read_plugin_files("a" * 40, "plugins/example")


def test_internal_repository_requires_allowlisted_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "PLUGIN_GIT_INTERNAL_ALLOWED_HOSTS", ["git.internal"])
    repository = SimpleNamespace(
        repository_url="https://untrusted.internal/platform/plugins",
        provider="gitlab",
        is_internal=True,
    )
    with pytest.raises(
        PluginRepositoryGitError, match="PLUGIN_GIT_INTERNAL_ALLOWED_HOSTS"
    ):
        validate_repository_url(repository)


def test_reporter_can_inspect_but_cannot_publish(
    test_db: Session,
    test_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = PluginRepositoryService()
    repository = _repository(test_db)
    _grant(test_db, repository, test_user, "Reporter")
    monkeypatch.setattr(service, "provider", lambda _repository: FakeProvider())
    monkeypatch.setattr(settings, "PLUGIN_REPOSITORY_PUBLISH_ENABLED", True)

    inspected = service.inspect(test_db, repository.id, test_user, "main", "branch")

    assert inspected.resolvedCommitSha == "a" * 40
    with pytest.raises(HTTPException) as error:
        service.create_publication(
            test_db,
            repository.id,
            test_user,
            "example-plugin",
            "main",
            "branch",
            "a" * 40,
        )
    assert error.value.status_code == 403


def test_developer_publication_rejects_moved_ref(
    test_db: Session,
    test_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = PluginRepositoryService()
    repository = _repository(test_db)
    _grant(test_db, repository, test_user, "Developer")
    monkeypatch.setattr(service, "provider", lambda _repository: FakeProvider("b" * 40))
    monkeypatch.setattr(settings, "PLUGIN_REPOSITORY_PUBLISH_ENABLED", True)

    with pytest.raises(HTTPException) as error:
        service.create_publication(
            test_db,
            repository.id,
            test_user,
            "example-plugin",
            "main",
            "branch",
            "a" * 40,
        )

    assert error.value.status_code == 409
    assert "moved" in str(error.value.detail)


def test_admin_has_owner_access_without_membership(
    test_db: Session, test_admin_user: User
) -> None:
    service = PluginRepositoryService()
    repository = _repository(test_db)

    authorized = service.authorized_repository(
        test_db, repository.id, test_admin_user, required=BaseRole.Developer
    )

    assert authorized.id == repository.id


def test_namespace_developer_membership_is_inherited(
    test_db: Session, test_user: User
) -> None:
    from app.services.external_entity_resolver import register_entity_resolver
    from app.services.share.namespace_entity_resolver import NamespaceEntityResolver

    register_entity_resolver("namespace", NamespaceEntityResolver)
    service = PluginRepositoryService()
    repository = _repository(test_db)
    namespace = Namespace(
        name="plugin-developers",
        display_name="Plugin Developers",
        owner_user_id=test_user.id,
        visibility="private",
        is_active=True,
    )
    test_db.add(namespace)
    test_db.flush()
    test_db.add_all(
        [
            ResourceMember(
                resource_type="Namespace",
                resource_id=namespace.id,
                entity_type="user",
                entity_id=str(test_user.id),
                user_id=test_user.id,
                role="Reporter",
                status=MemberStatus.APPROVED.value,
            ),
            ResourceMember(
                resource_type=ResourceType.PLUGIN_REPOSITORY.value,
                resource_id=repository.id,
                entity_type="namespace",
                entity_id=str(namespace.id),
                role="Developer",
                status=MemberStatus.APPROVED.value,
            ),
        ]
    )
    test_db.commit()

    assert service.user_role(test_db, repository.id, test_user.id) == "Developer"


def test_source_repository_conflict_blocks_candidate(test_db: Session) -> None:
    service = PluginRepositoryService()
    repository = _repository(test_db)
    test_db.add(
        Plugin(
            slug="example-plugin",
            name="example-plugin",
            display_name="Example Plugin",
            source_type="native",
            source_provider="wework",
            owner_user_id=0,
            source_repository_id=repository.id + 1,
            listing_type="plugin",
            keywords_json=[],
            interface_json={},
        )
    )
    test_db.commit()

    candidate = service._candidate_item(
        test_db, repository, FakeProvider().inspect_plugins("a" * 40)[0]
    )

    assert candidate.publishable is False
    assert "different source repository" in (candidate.blockedReason or "")


def test_provider_file_packaging_is_deterministic() -> None:
    publisher = OfficialPluginPublisher()
    files = {
        ".codex-plugin/plugin.json": (b'{"name":"example-plugin","version":"1.0.0"}'),
        "skills/example/SKILL.md": b"# Example\n",
    }

    first = publisher.build_package_from_files(files)
    second = publisher.build_package_from_files(dict(reversed(list(files.items()))))

    assert first.sha256 == second.sha256
    assert first.package == second.package


def test_repository_credential_is_encrypted_and_masked(test_db: Session) -> None:
    service = PluginRepositoryService()
    repository = _repository(test_db)
    repository.credential_encrypted = service._encrypt("secret-token")
    test_db.commit()

    item = service.repository_item(repository)

    assert repository.credential_encrypted != "secret-token"
    assert item.hasCredential is True
    assert "credential" not in item.model_dump()


def test_worker_scan_failure_marks_audit_without_release(
    test_db: Session,
    test_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = PluginRepositoryService()
    repository = _repository(test_db)
    publication = PluginRepositoryPublication(
        repository_id=repository.id,
        plugin_slug="example-plugin",
        requested_ref="main",
        ref_kind="branch",
        commit_sha="a" * 40,
        version="1.0.0",
        status="queued",
        requested_by_user_id=test_user.id,
    )
    test_db.add(publication)
    test_db.commit()
    monkeypatch.setattr(settings, "PLUGIN_REPOSITORY_PUBLISH_ENABLED", True)
    monkeypatch.setattr(service, "provider", lambda _repository: FakeProvider())
    monkeypatch.setattr(
        "app.services.plugin_repository_service.official_plugin_publisher.build_package_from_files",
        lambda _files: (_ for _ in ()).throw(ValueError("security scan failed")),
    )

    with pytest.raises(ValueError, match="security scan failed"):
        service.process_publication(test_db, publication.id)

    test_db.refresh(publication)
    assert publication.status == "failed"
    assert "security scan failed" in publication.error_message
    assert test_db.query(PluginRelease).count() == 0
