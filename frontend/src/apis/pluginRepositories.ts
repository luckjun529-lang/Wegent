// SPDX-FileCopyrightText: 2026 Weibo, Inc.
//
// SPDX-License-Identifier: Apache-2.0

import { apiClient } from './client'

export type PluginRepositoryProvider = 'github' | 'gitlab'
export type PluginRepositoryVisibility = 'public' | 'workspace'
export type PluginRepositoryRole = 'Owner' | 'Maintainer' | 'Developer' | 'Reporter'
export type GitRefKind = 'branch' | 'tag'

export interface PluginRepository {
  id: number
  name: string
  provider: PluginRepositoryProvider
  repositoryUrl: string
  visibility: PluginRepositoryVisibility
  defaultRef: string
  marketplacePath: string
  allowedBranchPatterns: string[]
  allowedTagPatterns: string[]
  hasCredential: boolean
  isInternal: boolean
  isEnabled: boolean
  myRole: PluginRepositoryRole | null
  lastValidatedAt: string | null
  lastError: string | null
  createdAt: string
  updatedAt: string
}

export interface PluginRepositoryCreate {
  name: string
  provider: PluginRepositoryProvider
  repositoryUrl: string
  visibility: PluginRepositoryVisibility
  defaultRef: string
  marketplacePath: string
  allowedBranchPatterns: string[]
  allowedTagPatterns: string[]
  credential?: string
  isInternal: boolean
  isEnabled: boolean
}

export interface PluginRepositoryMember {
  id?: number
  entityType: 'user' | 'namespace'
  entityId: string
  displayName: string
  role: PluginRepositoryRole
}

export interface PluginRepositoryRef {
  name: string
  kind: GitRefKind
  commitSha: string
}

export interface PluginCandidate {
  slug: string
  displayName: string
  version: string
  path: string
  listingType: 'plugin' | 'skill'
  currentVersion: string | null
  sourceRepositoryId: number | null
  publishable: boolean
  blockedReason: string | null
}

export interface PluginRepositoryInspection {
  repositoryId: number
  requestedRef: string
  refKind: GitRefKind
  resolvedCommitSha: string
  candidates: PluginCandidate[]
}

export type PluginPublicationStatus =
  | 'queued'
  | 'fetching'
  | 'validating'
  | 'publishing'
  | 'published'
  | 'failed'

export interface PluginPublication {
  id: number
  repositoryId: number
  pluginSlug: string
  requestedRef: string
  refKind: GitRefKind
  commitSha: string
  version: string | null
  status: PluginPublicationStatus
  requestedByUserId: number
  pluginId: number | null
  releaseId: number | null
  packageSha256: string | null
  errorCode: string | null
  errorMessage: string | null
  createdAt: string
  startedAt: string | null
  finishedAt: string | null
  updatedAt: string
}

export const pluginRepositoryApis = {
  listAdmin: async () => apiClient.get<{ items: PluginRepository[] }>('/admin/plugin-repositories'),
  create: async (data: PluginRepositoryCreate) =>
    apiClient.post<PluginRepository>('/admin/plugin-repositories', data),
  update: async (id: number, data: Record<string, unknown>) =>
    apiClient.patch<PluginRepository>(`/admin/plugin-repositories/${id}`, data),
  validate: async (id: number) =>
    apiClient.post<PluginRepository>(`/admin/plugin-repositories/${id}/validate`),
  listMembers: async (id: number) =>
    apiClient.get<{ items: PluginRepositoryMember[] }>(`/admin/plugin-repositories/${id}/members`),
  replaceMembers: async (id: number, items: PluginRepositoryMember[]) =>
    apiClient.put<{ items: PluginRepositoryMember[] }>(`/admin/plugin-repositories/${id}/members`, {
      items,
    }),
  listAccessible: async () =>
    apiClient.get<{ items: PluginRepository[] }>('/developer/plugins/repositories'),
  listRefs: async (id: number) =>
    apiClient.get<{ items: PluginRepositoryRef[] }>(`/developer/plugins/repositories/${id}/refs`),
  inspect: async (id: number, ref: string, kind: GitRefKind) =>
    apiClient.post<PluginRepositoryInspection>(`/developer/plugins/repositories/${id}/inspect`, {
      ref,
      kind,
    }),
  publish: async (
    id: number,
    data: { slug: string; ref: string; kind: GitRefKind; expectedCommitSha: string }
  ) =>
    apiClient.post<PluginPublication>(`/developer/plugins/repositories/${id}/publications`, data),
  listPublications: async (id: number) =>
    apiClient.get<{ items: PluginPublication[] }>(
      `/developer/plugins/repositories/${id}/publications`
    ),
  getPublication: async (id: number) =>
    apiClient.get<PluginPublication>(`/developer/plugins/publications/${id}`),
  retryPublication: async (id: number) =>
    apiClient.post<PluginPublication>(`/developer/plugins/publications/${id}/retry`),
}
