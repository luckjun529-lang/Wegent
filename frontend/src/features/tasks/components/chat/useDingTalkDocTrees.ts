// SPDX-FileCopyrightText: 2026 Weibo, Inc.
//
// SPDX-License-Identifier: Apache-2.0

'use client'

import { useCallback, useEffect, useRef, useState } from 'react'

import { dingtalkDocApi } from '@/apis/dingtalk-doc'
import { useTranslation } from '@/hooks/useTranslation'
import type { DingtalkDocNode } from '@/types/dingtalk-doc'

export function useDingTalkDocTrees({ enabled = true }: { enabled?: boolean } = {}) {
  const { t } = useTranslation('chat')

  const [nodes, setNodes] = useState<DingtalkDocNode[]>([])
  const [totalCount, setTotalCount] = useState(0)
  const [loading, setLoading] = useState(true)
  const [syncing, setSyncing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [isConfigured, setIsConfigured] = useState(true)
  const [lastSyncedAt, setLastSyncedAt] = useState<string | null>(null)
  const [isAuthenticated, setIsAuthenticated] = useState(false)
  const [authStatus, setAuthStatus] = useState<string>('unauthenticated')

  const [wikispaceNodes, setWikispaceNodes] = useState<DingtalkDocNode[]>([])
  const [wikispaceTotalCount, setWikispaceTotalCount] = useState(0)
  const [wikispaceLoading, setWikispaceLoading] = useState(true)
  const [wikispaceSyncing, setWikispaceSyncing] = useState(false)
  const [wikispaceError, setWikispaceError] = useState<string | null>(null)
  const [wikispaceConfigured, setWikispaceConfigured] = useState(false)
  const [wikispaceLastSyncedAt, setWikispaceLastSyncedAt] = useState<string | null>(null)

  const [teamFileNodes, setTeamFileNodes] = useState<DingtalkDocNode[]>([])
  const [teamFileTotalCount, setTeamFileTotalCount] = useState(0)
  const [teamFileLoading, setTeamFileLoading] = useState(true)
  const [teamFileSyncing, setTeamFileSyncing] = useState(false)
  const [teamFileError, setTeamFileError] = useState<string | null>(null)
  const [teamFileConfigured, setTeamFileConfigured] = useState(false)
  const [teamFileLastSyncedAt, setTeamFileLastSyncedAt] = useState<string | null>(null)
  const authStatusRequestRef = useRef<ReturnType<typeof dingtalkDocApi.getAuthStatus> | null>(null)

  const refreshAuthStatus = useCallback(async () => {
    if (!authStatusRequestRef.current) {
      authStatusRequestRef.current = dingtalkDocApi.getAuthStatus().finally(() => {
        authStatusRequestRef.current = null
      })
    }
    const status = await authStatusRequestRef.current
    setIsAuthenticated(status.is_authenticated)
    setAuthStatus(status.auth_status)
    return status
  }, [])

  const fetchDocs = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const auth = await refreshAuthStatus()
      if (!auth.is_authenticated) {
        setNodes([])
        setTotalCount(0)
        setIsConfigured(false)
        setLastSyncedAt(null)
        return
      }
      const [tree, status] = await Promise.all([
        dingtalkDocApi.getDocs(),
        dingtalkDocApi.getSyncStatus(),
      ])
      setNodes(tree.nodes)
      setTotalCount(tree.total_count)
      setIsConfigured(status.is_configured)
      setLastSyncedAt(status.last_synced_at)
    } catch (err) {
      console.error('Failed to fetch DingTalk docs:', err)
      setError(t('dingtalkDocs.loadFailed'))
    } finally {
      setLoading(false)
    }
  }, [refreshAuthStatus, t])

  const fetchWikispace = useCallback(async () => {
    setWikispaceLoading(true)
    setWikispaceError(null)
    try {
      const auth = await refreshAuthStatus()
      if (!auth.is_authenticated) {
        setWikispaceNodes([])
        setWikispaceTotalCount(0)
        setWikispaceConfigured(false)
        setWikispaceLastSyncedAt(null)
        return
      }
      const [tree, status] = await Promise.all([
        dingtalkDocApi.getWikispaceNodes(),
        dingtalkDocApi.getWikispaceSyncStatus(),
      ])
      setWikispaceNodes(tree.nodes)
      setWikispaceTotalCount(tree.total_count)
      setWikispaceConfigured(status.is_configured)
      setWikispaceLastSyncedAt(status.last_synced_at)
    } catch (err) {
      console.error('Failed to load DingTalk wikispace:', err)
      setWikispaceError(t('dingtalkDocs.loadFailed'))
    } finally {
      setWikispaceLoading(false)
    }
  }, [refreshAuthStatus, t])

  const fetchTeamFiles = useCallback(async () => {
    setTeamFileLoading(true)
    setTeamFileError(null)
    try {
      const auth = await refreshAuthStatus()
      if (!auth.is_authenticated) {
        setTeamFileNodes([])
        setTeamFileTotalCount(0)
        setTeamFileConfigured(false)
        setTeamFileLastSyncedAt(null)
        return
      }
      const [tree, status] = await Promise.all([
        dingtalkDocApi.getTeamFiles(),
        dingtalkDocApi.getTeamFilesSyncStatus(),
      ])
      setTeamFileNodes(tree.nodes)
      setTeamFileTotalCount(tree.total_count)
      setTeamFileConfigured(status.is_configured)
      setTeamFileLastSyncedAt(status.last_synced_at)
    } catch (err) {
      console.error('Failed to load DingTalk team files:', err)
      setTeamFileError(t('dingtalkDocs.loadFailed'))
    } finally {
      setTeamFileLoading(false)
    }
  }, [refreshAuthStatus, t])

  useEffect(() => {
    if (!enabled) return
    fetchDocs()
    fetchWikispace()
    fetchTeamFiles()
  }, [enabled, fetchDocs, fetchTeamFiles, fetchWikispace])

  const syncDocs = useCallback(async () => {
    setSyncing(true)
    setError(null)
    try {
      await dingtalkDocApi.syncDocs()
      await fetchDocs()
    } catch (err) {
      console.error('Failed to sync DingTalk docs:', err)
      setError(t('dingtalkDocs.syncFailed'))
    } finally {
      setSyncing(false)
    }
  }, [fetchDocs, t])

  const syncWikispace = useCallback(async () => {
    setWikispaceSyncing(true)
    setWikispaceError(null)
    try {
      await dingtalkDocApi.syncWikispaceNodes()
      await fetchWikispace()
    } catch (err) {
      console.error('Failed to sync DingTalk wikispace:', err)
      setWikispaceError(t('dingtalkDocs.syncFailed'))
    } finally {
      setWikispaceSyncing(false)
    }
  }, [fetchWikispace, t])

  const syncTeamFiles = useCallback(async () => {
    setTeamFileSyncing(true)
    setTeamFileError(null)
    try {
      await dingtalkDocApi.syncTeamFiles()
      await fetchTeamFiles()
    } catch (err) {
      console.error('Failed to sync DingTalk team files:', err)
      setTeamFileError(t('dingtalkDocs.syncFailed'))
    } finally {
      setTeamFileSyncing(false)
    }
  }, [fetchTeamFiles, t])

  const syncAllAfterAuth = useCallback(async () => {
    setIsAuthenticated(true)
    setAuthStatus('authenticated')
    setIsConfigured(true)
    setWikispaceConfigured(true)
    setTeamFileConfigured(true)
    setError(null)
    setWikispaceError(null)
    setTeamFileError(null)
    await Promise.allSettled([syncDocs(), syncWikispace(), syncTeamFiles()])
  }, [syncDocs, syncTeamFiles, syncWikispace])

  return {
    nodes,
    totalCount,
    loading,
    syncing,
    error,
    isConfigured,
    isAuthenticated,
    authStatus,
    lastSyncedAt,
    refreshAuthStatus,
    fetchDocs,
    syncDocs,
    wikispaceNodes,
    wikispaceTotalCount,
    wikispaceLoading,
    wikispaceSyncing,
    wikispaceError,
    wikispaceConfigured,
    wikispaceLastSyncedAt,
    fetchWikispace,
    syncWikispace,
    teamFileNodes,
    teamFileTotalCount,
    teamFileLoading,
    teamFileSyncing,
    teamFileError,
    teamFileConfigured,
    teamFileLastSyncedAt,
    fetchTeamFiles,
    syncTeamFiles,
    syncAllAfterAuth,
  }
}
