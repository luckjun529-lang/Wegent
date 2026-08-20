# SPDX-FileCopyrightText: 2026 Weibo, Inc.
#
# SPDX-License-Identifier: Apache-2.0

"""Read trusted plugin repositories through GitHub and GitLab APIs."""

from __future__ import annotations

import base64
import fnmatch
import json
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Literal
from urllib.parse import quote, urlparse

import httpx

from app.core.config import settings
from app.models.plugin_marketplace import PluginRepository
from app.services.plugin_upstream_fetch import validate_upstream_url

MAX_REPOSITORY_FILES = 5000
MAX_REPOSITORY_SOURCE_BYTES = 200 * 1024 * 1024
GIT_LFS_PREFIX = b"version https://git-lfs.github.com/spec/v1"


class PluginRepositoryGitError(ValueError):
    """Raised when a repository cannot be read safely or consistently."""


@dataclass(frozen=True)
class RepositoryCoordinates:
    host: str
    owner: str
    repository: str
    base_url: str


@dataclass(frozen=True)
class RepositoryRef:
    name: str
    kind: Literal["branch", "tag"]
    commit_sha: str


@dataclass(frozen=True)
class RepositoryFile:
    path: str
    mode: str
    blob_id: str


@dataclass(frozen=True)
class RepositoryPluginCandidate:
    slug: str
    display_name: str
    version: str
    path: str
    listing_type: Literal["plugin", "skill"]


def validate_repository_url(
    repository: PluginRepository | Any,
) -> RepositoryCoordinates:
    raw_url = str(repository.repository_url).strip()
    parsed = urlparse(raw_url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise PluginRepositoryGitError("Repository URL must use HTTPS")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise PluginRepositoryGitError(
            "Repository URL cannot contain credentials or query data"
        )

    host = parsed.hostname.lower()
    if repository.is_internal:
        allowed_hosts = {
            value.strip().lower()
            for value in settings.PLUGIN_GIT_INTERNAL_ALLOWED_HOSTS
            if value.strip()
        }
        if host not in allowed_hosts:
            raise PluginRepositoryGitError(
                "Internal repository host is not in PLUGIN_GIT_INTERNAL_ALLOWED_HOSTS"
            )
    else:
        validate_upstream_url(raw_url)

    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) < 2:
        raise PluginRepositoryGitError(
            "Repository URL must include owner and repository"
        )
    repository_name = parts[-1].removesuffix(".git")
    owner = "/".join(parts[:-1])
    if not repository_name or not owner:
        raise PluginRepositoryGitError("Repository URL is incomplete")
    if repository.provider == "github" and host != "github.com":
        raise PluginRepositoryGitError("The GitHub provider supports github.com only")
    return RepositoryCoordinates(
        host=host,
        owner=owner,
        repository=repository_name,
        base_url=f"{parsed.scheme}://{parsed.netloc}",
    )


def normalize_repository_path(value: str, *, field: str) -> str:
    raw = value.strip().replace("\\", "/")
    parsed = urlparse(raw)
    if parsed.scheme or parsed.netloc:
        raise PluginRepositoryGitError(f"{field} must be a local repository path")
    while raw.startswith("./"):
        raw = raw[2:]
    path = PurePosixPath(raw)
    if (
        not raw
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise PluginRepositoryGitError(
            f"{field} must be a safe repository-relative path"
        )
    return path.as_posix()


class PluginRepositoryGitProvider:
    """Provider-neutral interface implemented by GitHub and GitLab."""

    def __init__(self, repository: PluginRepository, credential: str = "") -> None:
        self.repository = repository
        self.coordinates = validate_repository_url(repository)
        self.credential = credential
        self.timeout = float(settings.REPOSITORY_READ_TIMEOUT_SECONDS)

    def list_refs(self) -> list[RepositoryRef]:
        refs = self._list_refs("branch") + self._list_refs("tag")
        return [item for item in refs if self._ref_allowed(item)]

    def resolve_ref(self, name: str, kind: Literal["branch", "tag"]) -> RepositoryRef:
        normalized = name.strip()
        if not normalized:
            raise PluginRepositoryGitError("Git ref is required")
        for item in self._list_refs(kind):
            if item.name == normalized:
                if not self._ref_allowed(item):
                    raise PluginRepositoryGitError(
                        "Git ref is not allowed for publication"
                    )
                return item
        raise PluginRepositoryGitError("Git ref was not found")

    def inspect_plugins(self, commit_sha: str) -> list[RepositoryPluginCandidate]:
        marketplace_path = normalize_repository_path(
            self.repository.marketplace_path, field="marketplacePath"
        )
        try:
            marketplace = json.loads(self.read_file(commit_sha, marketplace_path))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PluginRepositoryGitError(
                "Marketplace manifest is not valid JSON"
            ) from exc
        entries = marketplace.get("plugins") if isinstance(marketplace, dict) else None
        if not isinstance(entries, list):
            raise PluginRepositoryGitError(
                "Marketplace manifest must contain a plugins array"
            )
        candidates: list[RepositoryPluginCandidate] = []
        seen: set[str] = set()
        for entry in entries:
            if not isinstance(entry, dict):
                raise PluginRepositoryGitError(
                    "Marketplace plugin entries must be objects"
                )
            slug = str(entry.get("name") or "").strip()
            source = entry.get("source")
            source_path = source.get("path") if isinstance(source, dict) else source
            if not slug or not isinstance(source_path, str):
                raise PluginRepositoryGitError(
                    "Marketplace plugin name and local path are required"
                )
            path = normalize_repository_path(source_path, field=f"Plugin {slug} path")
            manifest = self._read_plugin_manifest(commit_sha, path)
            manifest_name = str(manifest.get("name") or "").strip()
            version = str(manifest.get("version") or "").strip()
            if slug != manifest_name:
                raise PluginRepositoryGitError(
                    f"Marketplace plugin {slug} does not match manifest name {manifest_name}"
                )
            if slug in seen:
                raise PluginRepositoryGitError(
                    f"Marketplace plugin {slug} is duplicated"
                )
            seen.add(slug)
            interface = manifest.get("interface")
            display_name = (
                str(interface.get("displayName") or "").strip()
                if isinstance(interface, dict)
                else ""
            )
            listing_type = self._listing_type(commit_sha, path)
            candidates.append(
                RepositoryPluginCandidate(
                    slug=slug,
                    display_name=display_name or slug,
                    version=version,
                    path=path,
                    listing_type=listing_type,
                )
            )
        return candidates

    def read_plugin_files(self, commit_sha: str, plugin_path: str) -> dict[str, bytes]:
        root = normalize_repository_path(plugin_path, field="plugin path")
        entries = self.list_tree(commit_sha, root)
        files: dict[str, bytes] = {}
        total_bytes = 0
        prefix = f"{root}/"
        for entry in entries:
            if entry.mode == "120000":
                raise PluginRepositoryGitError(
                    f"Symbolic links are not allowed: {entry.path}"
                )
            if entry.mode == "160000":
                raise PluginRepositoryGitError(
                    f"Git submodules are not allowed: {entry.path}"
                )
            if not entry.path.startswith(prefix):
                raise PluginRepositoryGitError(
                    "Git provider returned a path outside the plugin"
                )
            relative = normalize_repository_path(
                entry.path[len(prefix) :], field="file path"
            )
            content = self.read_blob(commit_sha, entry)
            if content.startswith(GIT_LFS_PREFIX):
                raise PluginRepositoryGitError(
                    f"Git LFS files are not supported: {entry.path}"
                )
            total_bytes += len(content)
            if total_bytes > MAX_REPOSITORY_SOURCE_BYTES:
                raise PluginRepositoryGitError(
                    "Plugin source exceeds the expanded size limit"
                )
            files[relative] = content
        if len(files) > MAX_REPOSITORY_FILES:
            raise PluginRepositoryGitError("Plugin source contains too many files")
        if not files:
            raise PluginRepositoryGitError("Plugin source directory is empty")
        return files

    def _read_plugin_manifest(
        self, commit_sha: str, plugin_path: str
    ) -> dict[str, Any]:
        for relative in (".codex-plugin/plugin.json", ".claude-plugin/plugin.json"):
            path = f"{plugin_path}/{relative}"
            try:
                content = self.read_file(commit_sha, path)
            except PluginRepositoryGitError as exc:
                if "not found" in str(exc).lower():
                    continue
                raise
            try:
                parsed = json.loads(content)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise PluginRepositoryGitError(
                    f"Plugin manifest is invalid: {path}"
                ) from exc
            if not isinstance(parsed, dict):
                raise PluginRepositoryGitError(
                    f"Plugin manifest must be an object: {path}"
                )
            return parsed
        raise PluginRepositoryGitError(
            f"Plugin manifest was not found under {plugin_path}"
        )

    def _listing_type(
        self, commit_sha: str, plugin_path: str
    ) -> Literal["plugin", "skill"]:
        entries = self.list_tree(commit_sha, plugin_path)
        skills = {
            PurePosixPath(item.path).parts[-2]
            for item in entries
            if item.path.endswith("/SKILL.md") and "/skills/" in f"/{item.path}"
        }
        non_skill_roots = {"commands", "agents", "hooks", "mcp", "mcps"}
        has_other = any(
            len(PurePosixPath(item.path).parts) > len(PurePosixPath(plugin_path).parts)
            and PurePosixPath(item.path).parts[len(PurePosixPath(plugin_path).parts)]
            in non_skill_roots
            for item in entries
        )
        return "skill" if len(skills) == 1 and not has_other else "plugin"

    def _ref_allowed(self, ref: RepositoryRef) -> bool:
        patterns = (
            self.repository.allowed_branch_patterns_json
            if ref.kind == "branch"
            else self.repository.allowed_tag_patterns_json
        )
        return any(
            fnmatch.fnmatchcase(ref.name, str(pattern)) for pattern in patterns or []
        )

    def _list_refs(self, kind: Literal["branch", "tag"]) -> list[RepositoryRef]:
        raise NotImplementedError

    def read_file(self, commit_sha: str, path: str) -> bytes:
        raise NotImplementedError

    def list_tree(self, commit_sha: str, path: str) -> list[RepositoryFile]:
        raise NotImplementedError

    def read_blob(self, commit_sha: str, entry: RepositoryFile) -> bytes:
        raise NotImplementedError


class GitHubPluginRepositoryProvider(PluginRepositoryGitProvider):
    @property
    def api_base(self) -> str:
        c = self.coordinates
        return f"https://api.github.com/repos/{c.owner}/{c.repository}"

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/vnd.github+json"}
        if self.credential:
            headers["Authorization"] = f"Bearer {self.credential}"
        return headers

    def _get(self, url: str, *, params: dict[str, Any] | None = None) -> httpx.Response:
        try:
            response = httpx.get(
                url,
                headers=self._headers(),
                params=params,
                timeout=self.timeout,
                follow_redirects=False,
            )
        except httpx.RequestError as exc:
            raise PluginRepositoryGitError("GitHub request failed") from exc
        if response.status_code == 404:
            raise PluginRepositoryGitError("Repository content was not found")
        if response.status_code in {401, 403}:
            raise PluginRepositoryGitError("GitHub repository authentication failed")
        if response.is_redirect:
            raise PluginRepositoryGitError("GitHub API redirects are not allowed")
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise PluginRepositoryGitError("GitHub request failed") from exc
        return response

    def _list_refs(self, kind: Literal["branch", "tag"]) -> list[RepositoryRef]:
        endpoint = "branches" if kind == "branch" else "tags"
        items: list[dict[str, Any]] = []
        for page in range(1, 51):
            response = self._get(
                f"{self.api_base}/{endpoint}",
                params={"per_page": 100, "page": page},
            )
            payload = response.json()
            if not isinstance(payload, list):
                raise PluginRepositoryGitError("GitHub returned an invalid ref list")
            items.extend(item for item in payload if isinstance(item, dict))
            if len(payload) < 100:
                break
        return [
            RepositoryRef(
                name=str(item["name"]),
                kind=kind,
                commit_sha=str(item["commit"]["sha"]),
            )
            for item in items
            if isinstance(item.get("commit"), dict)
        ]

    def read_file(self, commit_sha: str, path: str) -> bytes:
        encoded = quote(path, safe="/")
        response = self._get(
            f"{self.api_base}/contents/{encoded}", params={"ref": commit_sha}
        )
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("type") != "file":
            raise PluginRepositoryGitError("Repository file was not found")
        content = payload.get("content")
        if not isinstance(content, str):
            raise PluginRepositoryGitError("Repository file content is unavailable")
        try:
            return base64.b64decode(content, validate=False)
        except ValueError as exc:
            raise PluginRepositoryGitError(
                "Repository file content is invalid"
            ) from exc

    def list_tree(self, commit_sha: str, path: str) -> list[RepositoryFile]:
        response = self._get(
            f"{self.api_base}/git/trees/{commit_sha}", params={"recursive": "1"}
        )
        payload = response.json()
        if payload.get("truncated"):
            raise PluginRepositoryGitError("GitHub repository tree is too large")
        prefix = f"{path.rstrip('/')}/"
        rows = payload.get("tree") or []
        files = [
            RepositoryFile(
                path=str(item["path"]),
                mode=str(item.get("mode") or ""),
                blob_id=str(item.get("sha") or ""),
            )
            for item in rows
            if isinstance(item, dict)
            and str(item.get("path") or "").startswith(prefix)
            and item.get("type") in {"blob", "commit"}
        ]
        if len(files) > MAX_REPOSITORY_FILES:
            raise PluginRepositoryGitError("Plugin source contains too many files")
        return files

    def read_blob(self, commit_sha: str, entry: RepositoryFile) -> bytes:
        del commit_sha
        response = self._get(f"{self.api_base}/git/blobs/{entry.blob_id}")
        payload = response.json()
        content = payload.get("content")
        if not isinstance(content, str):
            raise PluginRepositoryGitError("GitHub blob content is unavailable")
        return base64.b64decode(content, validate=False)


class GitLabPluginRepositoryProvider(PluginRepositoryGitProvider):
    @property
    def api_base(self) -> str:
        c = self.coordinates
        project = quote(f"{c.owner}/{c.repository}", safe="")
        return f"{c.base_url}/api/v4/projects/{project}"

    def _headers(self) -> dict[str, str]:
        return {"PRIVATE-TOKEN": self.credential} if self.credential else {}

    def _get(self, url: str, *, params: dict[str, Any] | None = None) -> httpx.Response:
        try:
            response = httpx.get(
                url,
                headers=self._headers(),
                params=params,
                timeout=self.timeout,
                follow_redirects=False,
            )
        except httpx.RequestError as exc:
            raise PluginRepositoryGitError("GitLab request failed") from exc
        if response.status_code == 404:
            raise PluginRepositoryGitError("Repository content was not found")
        if response.status_code in {401, 403}:
            raise PluginRepositoryGitError("GitLab repository authentication failed")
        if response.is_redirect:
            raise PluginRepositoryGitError("GitLab API redirects are not allowed")
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise PluginRepositoryGitError("GitLab request failed") from exc
        return response

    def _paginated(self, url: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for page in range(1, 51):
            response = self._get(url, params={**params, "per_page": 100, "page": page})
            items = response.json()
            if not isinstance(items, list):
                raise PluginRepositoryGitError(
                    "GitLab returned an invalid list response"
                )
            rows.extend(item for item in items if isinstance(item, dict))
            if len(items) < 100:
                break
        return rows

    def _list_refs(self, kind: Literal["branch", "tag"]) -> list[RepositoryRef]:
        endpoint = "branches" if kind == "branch" else "tags"
        items = self._paginated(f"{self.api_base}/repository/{endpoint}", {})
        return [
            RepositoryRef(
                name=str(item["name"]),
                kind=kind,
                commit_sha=str(item["commit"]["id"]),
            )
            for item in items
            if isinstance(item.get("commit"), dict)
        ]

    def read_file(self, commit_sha: str, path: str) -> bytes:
        encoded = quote(path, safe="")
        return self._get(
            f"{self.api_base}/repository/files/{encoded}/raw",
            params={"ref": commit_sha},
        ).content

    def list_tree(self, commit_sha: str, path: str) -> list[RepositoryFile]:
        items = self._paginated(
            f"{self.api_base}/repository/tree",
            {"path": path, "ref": commit_sha, "recursive": True},
        )
        files = [
            RepositoryFile(
                path=str(item["path"]),
                mode=str(item.get("mode") or ""),
                blob_id=str(item.get("id") or ""),
            )
            for item in items
            if item.get("type") in {"blob", "commit"}
        ]
        if len(files) > MAX_REPOSITORY_FILES:
            raise PluginRepositoryGitError("Plugin source contains too many files")
        return files

    def read_blob(self, commit_sha: str, entry: RepositoryFile) -> bytes:
        return self.read_file(commit_sha, entry.path)


def create_plugin_repository_provider(
    repository: PluginRepository, credential: str = ""
) -> PluginRepositoryGitProvider:
    if repository.provider == "github":
        return GitHubPluginRepositoryProvider(repository, credential)
    if repository.provider == "gitlab":
        return GitLabPluginRepositoryProvider(repository, credential)
    raise PluginRepositoryGitError("Unsupported plugin repository provider")
