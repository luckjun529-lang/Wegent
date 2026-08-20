# SPDX-FileCopyrightText: 2026 Weibo, Inc.
#
# SPDX-License-Identifier: Apache-2.0

"""Contracts for administrator-managed plugin source repositories."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class PluginRepositoryCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    provider: Literal["github", "gitlab"]
    repositoryUrl: str = Field(min_length=1, max_length=500)
    visibility: Literal["public", "workspace"]
    defaultRef: str = Field(default="main", min_length=1, max_length=200)
    marketplacePath: str = Field(
        default=".agents/plugins/marketplace.json", min_length=1, max_length=300
    )
    allowedBranchPatterns: list[str] = Field(default_factory=list, max_length=20)
    allowedTagPatterns: list[str] = Field(default_factory=lambda: ["*"], max_length=20)
    credential: str | None = Field(default=None, max_length=2000)
    isInternal: bool = False
    isEnabled: bool = True

    @field_validator("allowedBranchPatterns", "allowedTagPatterns", mode="after")
    @classmethod
    def validate_patterns(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            item = value.strip()
            if not item or len(item) > 200:
                raise ValueError("Ref patterns must contain 1-200 characters")
            if item not in normalized:
                normalized.append(item)
        return normalized

    @model_validator(mode="after")
    def apply_default_branch_pattern(self) -> "PluginRepositoryCreateRequest":
        if not self.allowedBranchPatterns:
            self.allowedBranchPatterns = [self.defaultRef]
        return self


class PluginRepositoryUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    repositoryUrl: str | None = Field(default=None, min_length=1, max_length=500)
    visibility: Literal["public", "workspace"] | None = None
    defaultRef: str | None = Field(default=None, min_length=1, max_length=200)
    marketplacePath: str | None = Field(default=None, min_length=1, max_length=300)
    allowedBranchPatterns: list[str] | None = Field(default=None, max_length=20)
    allowedTagPatterns: list[str] | None = Field(default=None, max_length=20)
    credentialAction: Literal["keep", "replace", "remove"] = "keep"
    credential: str | None = Field(default=None, max_length=2000)
    isInternal: bool | None = None
    isEnabled: bool | None = None

    @field_validator("allowedBranchPatterns", "allowedTagPatterns", mode="after")
    @classmethod
    def validate_patterns(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return None
        return PluginRepositoryCreateRequest.validate_patterns(values)

    @model_validator(mode="after")
    def validate_credential_action(self) -> "PluginRepositoryUpdateRequest":
        if self.credentialAction == "replace" and not (self.credential or "").strip():
            raise ValueError("credential is required when credentialAction is replace")
        if self.credentialAction != "replace" and self.credential is not None:
            raise ValueError("credential is only accepted when replacing it")
        return self


class PluginRepositoryItem(BaseModel):
    id: int
    name: str
    provider: Literal["github", "gitlab"]
    repositoryUrl: str
    visibility: Literal["public", "workspace"]
    defaultRef: str
    marketplacePath: str
    allowedBranchPatterns: list[str]
    allowedTagPatterns: list[str]
    hasCredential: bool
    isInternal: bool
    isEnabled: bool
    myRole: str | None = None
    lastValidatedAt: datetime | None = None
    lastError: str | None = None
    createdAt: datetime
    updatedAt: datetime


class PluginRepositoryListResponse(BaseModel):
    items: list[PluginRepositoryItem]


class PluginRepositoryMemberRequest(BaseModel):
    entityType: Literal["user", "namespace"]
    entityId: str = Field(min_length=1, max_length=100)
    displayName: str = Field(default="", max_length=100)
    role: Literal["Owner", "Maintainer", "Developer", "Reporter"] = "Developer"


class PluginRepositoryMembersUpdateRequest(BaseModel):
    items: list[PluginRepositoryMemberRequest] = Field(
        default_factory=list, max_length=200
    )


class PluginRepositoryMemberItem(PluginRepositoryMemberRequest):
    id: int


class PluginRepositoryMembersResponse(BaseModel):
    items: list[PluginRepositoryMemberItem]


class PluginRepositoryRefItem(BaseModel):
    name: str
    kind: Literal["branch", "tag"]
    commitSha: str


class PluginRepositoryRefsResponse(BaseModel):
    items: list[PluginRepositoryRefItem]


class PluginRepositoryInspectRequest(BaseModel):
    ref: str = Field(min_length=1, max_length=200)
    kind: Literal["branch", "tag"]


class PluginRepositoryCandidateItem(BaseModel):
    slug: str
    displayName: str
    version: str
    path: str
    listingType: Literal["plugin", "skill"] = "plugin"
    currentVersion: str | None = None
    sourceRepositoryId: int | None = None
    publishable: bool = True
    blockedReason: str | None = None


class PluginRepositoryInspectResponse(BaseModel):
    repositoryId: int
    requestedRef: str
    refKind: Literal["branch", "tag"]
    resolvedCommitSha: str
    candidates: list[PluginRepositoryCandidateItem]


class PluginRepositoryPublishRequest(BaseModel):
    slug: str = Field(min_length=1, max_length=100)
    ref: str = Field(min_length=1, max_length=200)
    kind: Literal["branch", "tag"]
    expectedCommitSha: str = Field(min_length=40, max_length=64)


class PluginRepositoryPublicationItem(BaseModel):
    id: int
    repositoryId: int
    pluginSlug: str
    requestedRef: str
    refKind: Literal["branch", "tag"]
    commitSha: str
    version: str | None = None
    status: Literal[
        "queued", "fetching", "validating", "publishing", "published", "failed"
    ]
    requestedByUserId: int
    pluginId: int | None = None
    releaseId: int | None = None
    packageSha256: str | None = None
    errorCode: str | None = None
    errorMessage: str | None = None
    createdAt: datetime
    startedAt: datetime | None = None
    finishedAt: datetime | None = None
    updatedAt: datetime


class PluginRepositoryPublicationListResponse(BaseModel):
    items: list[PluginRepositoryPublicationItem]
