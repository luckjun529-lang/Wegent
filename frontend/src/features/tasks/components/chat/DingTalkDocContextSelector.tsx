// SPDX-FileCopyrightText: 2025 Weibo, Inc.
//
// SPDX-License-Identifier: Apache-2.0

/**
 * DingTalkDocContextSelector - Tree-based DingTalk document selector for chat context.
 *
 * Renders the synced DingTalk document tree with checkboxes.
 * Selecting a folder automatically selects all its descendant nodes.
 * Supports My Documents, Team Files, and Knowledge Base sections.
 */

'use client'

import React, { useState, useCallback, useEffect, useMemo, useRef } from 'react'
import {
  Folder,
  FolderOpen,
  ChevronRight,
  ChevronDown,
  FileText,
  RefreshCw,
  ExternalLink,
  Check,
  Minus,
  Search,
  KeyRound,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { useTranslation } from '@/hooks/useTranslation'
import { dingtalkDocApi } from '@/apis/dingtalk-doc'
import { getSafeHttpUrl } from '@/utils/safe-url'
import type { DingtalkDocNode } from '@/types/dingtalk-doc'
import type { DingTalkDocContext } from '@/types/context'
import {
  buildDingTalkDocContext,
  collectDescendants,
  filterDingTalkNodes,
  getDingTalkSelectionKey,
  isNodeFullySelected,
  isNodePartiallySelected,
  MAX_DINGTALK_DOC_CONTEXTS,
} from './dingtalk-context-utils'
import { useDingTalkDocTrees } from './useDingTalkDocTrees'

interface TreeNodeItemProps {
  node: DingtalkDocNode
  level: number
  selectedIds: Set<string>
  onToggle: (node: DingtalkDocNode) => void
  searchQuery: string
}

/** Recursive tree node item with checkbox. */
export function DingtalkContextTreeNode({
  node,
  level,
  selectedIds,
  onToggle,
  searchQuery,
}: TreeNodeItemProps) {
  const isFolder = node.node_type === 'folder'
  const [isExpanded, setIsExpanded] = useState(level === 0)
  const selectionKey = getDingTalkSelectionKey(node.source, node.dingtalk_node_id)
  const isSelected = isFolder
    ? isNodeFullySelected(node, selectedIds)
    : selectedIds.has(selectionKey)
  const isPartial = isFolder ? isNodePartiallySelected(node, selectedIds) : false
  const hasChildren = isFolder && node.children && node.children.length > 0
  const safeDocUrl = getSafeHttpUrl(node.doc_url)

  // Auto-expand when searching
  useEffect(() => {
    if (searchQuery) setIsExpanded(true)
  }, [searchQuery])

  const handleToggle = useCallback((e: React.MouseEvent) => {
    e.stopPropagation()
    setIsExpanded(prev => !prev)
  }, [])

  const handleCheck = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation()
      onToggle(node)
    },
    [node, onToggle]
  )

  return (
    <div>
      <div
        className={cn(
          'flex items-center gap-1.5 py-1.5 rounded-md text-sm transition-colors cursor-pointer',
          'hover:bg-surface-hover group'
        )}
        style={{ paddingLeft: `${level * 16 + 8}px`, paddingRight: '8px' }}
        onClick={handleCheck}
        role="button"
        tabIndex={0}
        onKeyDown={e => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault()
            onToggle(node)
          }
        }}
        data-testid={`dingtalk-ctx-node-${node.source}-${node.dingtalk_node_id}`}
      >
        {/* Expand toggle for folders */}
        {isFolder && hasChildren ? (
          <button
            type="button"
            className="flex-shrink-0 w-5 h-5 flex items-center justify-center hover:bg-muted rounded"
            onClick={handleToggle}
            data-testid={`dingtalk-ctx-expand-${node.source}-${node.dingtalk_node_id}`}
          >
            {isExpanded ? (
              <ChevronDown className="w-3 h-3 text-text-muted" />
            ) : (
              <ChevronRight className="w-3 h-3 text-text-muted" />
            )}
          </button>
        ) : (
          <span className="flex-shrink-0 w-5 h-5" />
        )}

        {/* Checkbox */}
        <div
          className={cn(
            'flex-shrink-0 w-4 h-4 rounded border-[1.5px] flex items-center justify-center transition-colors',
            isSelected
              ? 'bg-primary border-primary'
              : isPartial
                ? 'bg-primary/20 border-primary'
                : 'border-border bg-base group-hover:border-primary/50'
          )}
        >
          {isSelected && <Check className="w-2.5 h-2.5 text-white stroke-[3]" />}
          {!isSelected && isPartial && <Minus className="w-2.5 h-2.5 text-primary stroke-[3]" />}
        </div>

        {/* Icon */}
        {isFolder ? (
          isExpanded ? (
            <FolderOpen className="w-3.5 h-3.5 flex-shrink-0 text-amber-500" />
          ) : (
            <Folder className="w-3.5 h-3.5 flex-shrink-0 text-amber-500" />
          )
        ) : (
          <FileText className="w-3.5 h-3.5 flex-shrink-0 text-text-muted" />
        )}

        {/* Name */}
        <span className="flex-1 truncate text-sm text-text-primary" title={node.name}>
          {node.name}
        </span>

        {/* External link for docs (visible on hover) */}
        {!isFolder && safeDocUrl && (
          <a
            href={safeDocUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="flex-shrink-0 opacity-0 group-hover:opacity-100 transition-opacity"
            onClick={e => e.stopPropagation()}
            aria-label={node.name}
            data-testid={`dingtalk-ctx-link-${node.source}-${node.dingtalk_node_id}`}
          >
            <ExternalLink className="w-3 h-3 text-text-muted hover:text-primary" />
          </a>
        )}
      </div>

      {/* Children */}
      {isFolder && hasChildren && isExpanded && (
        <div>
          {node.children!.map(child => (
            <DingtalkContextTreeNode
              key={getDingTalkSelectionKey(child.source, child.dingtalk_node_id)}
              node={child}
              level={level + 1}
              selectedIds={selectedIds}
              onToggle={onToggle}
              searchQuery={searchQuery}
            />
          ))}
        </div>
      )}
    </div>
  )
}

interface DingTalkDocContextSelectorProps {
  selectedContexts: Set<string>
  onSelect: (context: DingTalkDocContext) => void
  onDeselect: (id: string) => void
  onSelectMultiple: (contexts: DingTalkDocContext[]) => void
  onDeselectMultiple: (ids: string[]) => void
}

/**
 * DingTalk document context selector panel.
 * Displays the synced document tree with checkboxes for multi-selection.
 * Supports My Documents, Team Files, and Knowledge Base.
 */
export function DingTalkDocContextSelector({
  selectedContexts,
  onSelect,
  onDeselect,
  onSelectMultiple,
  onDeselectMultiple,
}: DingTalkDocContextSelectorProps) {
  const { t } = useTranslation('chat')
  const [activeSection, setActiveSection] = useState<'my-docs' | 'team-files' | 'wikispace'>(
    'my-docs'
  )
  const [searchQuery, setSearchQuery] = useState('')
  const [selectionError, setSelectionError] = useState<string | null>(null)
  const [authSessionId, setAuthSessionId] = useState<string | null>(null)
  const [verificationUrl, setVerificationUrl] = useState<string | null>(null)
  const [userCode, setUserCode] = useState<string | null>(null)
  const [authPolling, setAuthPolling] = useState(false)
  const [authError, setAuthError] = useState<string | null>(null)
  const authStartingRef = useRef(false)
  const {
    nodes,
    loading,
    syncing,
    error,
    isConfigured,
    isAuthenticated,
    authStatus,
    lastSyncedAt,
    fetchDocs,
    syncDocs,
    wikispaceNodes,
    wikispaceLoading,
    wikispaceSyncing,
    wikispaceError,
    wikispaceConfigured,
    wikispaceLastSyncedAt,
    fetchWikispace,
    syncWikispace,
    teamFileNodes,
    teamFileLoading,
    teamFileSyncing,
    teamFileError,
    teamFileConfigured,
    teamFileLastSyncedAt,
    fetchTeamFiles,
    syncTeamFiles,
    syncAllAfterAuth,
  } = useDingTalkDocTrees()

  const selectedDocCount = useMemo(() => selectedContexts.size, [selectedContexts])

  const completeAuth = useCallback(async () => {
    authStartingRef.current = false
    setAuthSessionId(null)
    setVerificationUrl(null)
    setUserCode(null)
    setAuthPolling(false)
    await syncAllAfterAuth()
  }, [syncAllAfterAuth])

  const handleToggle = useCallback(
    (node: DingtalkDocNode) => {
      const selectionKey = getDingTalkSelectionKey(node.source, node.dingtalk_node_id)
      setSelectionError(null)

      if (node.node_type === 'folder') {
        const allIds = collectDescendants(node)
        const allSelected = allIds.every(id => selectedContexts.has(id))

        if (allSelected) {
          onDeselectMultiple(allIds)
        } else {
          const toAdd: DingTalkDocContext[] = []
          const addNode = (n: DingtalkDocNode) => {
            const childSelectionKey = getDingTalkSelectionKey(n.source, n.dingtalk_node_id)
            if (n.node_type !== 'folder' && !selectedContexts.has(childSelectionKey)) {
              toAdd.push(buildDingTalkDocContext(n))
            }
            if (n.children) {
              n.children.forEach(addNode)
            }
          }
          addNode(node)
          const availableSlots = MAX_DINGTALK_DOC_CONTEXTS - selectedDocCount
          if (availableSlots <= 0) {
            setSelectionError(t('dingtalkDocs.maxSelected', { count: MAX_DINGTALK_DOC_CONTEXTS }))
            return
          }
          const limited = toAdd.slice(0, availableSlots)
          if (limited.length < toAdd.length) {
            setSelectionError(t('dingtalkDocs.maxSelected', { count: MAX_DINGTALK_DOC_CONTEXTS }))
          }
          if (limited.length > 0) {
            onSelectMultiple(limited)
          }
        }
      } else {
        if (selectedContexts.has(selectionKey)) {
          onDeselect(selectionKey)
        } else {
          if (selectedDocCount >= MAX_DINGTALK_DOC_CONTEXTS) {
            setSelectionError(t('dingtalkDocs.maxSelected', { count: MAX_DINGTALK_DOC_CONTEXTS }))
            return
          }
          onSelect(buildDingTalkDocContext(node))
        }
      }
    },
    [
      selectedContexts,
      selectedDocCount,
      onSelect,
      onDeselect,
      onSelectMultiple,
      onDeselectMultiple,
      t,
    ]
  )

  const handleStartAuth = useCallback(async () => {
    if (authPolling || authStartingRef.current) return
    authStartingRef.current = true
    setAuthError(null)
    setAuthPolling(true)
    try {
      const result = await dingtalkDocApi.startDeviceLogin()
      if (result.is_authenticated) {
        await completeAuth()
        return
      }

      const url = getSafeHttpUrl(result.verification_url)
      const sessionId = result.session_id ?? null
      if (!url || !sessionId) {
        authStartingRef.current = false
        setAuthError(t('dingtalkDocs.authFailed'))
        setAuthPolling(false)
        return
      }

      setVerificationUrl(url)
      setUserCode(result.user_code ?? null)
      setAuthSessionId(sessionId)

      window.open(url, '_blank', 'noopener,noreferrer')
    } catch (err) {
      console.error('Failed to start DingTalk DWS device login:', err)
      authStartingRef.current = false
      setAuthError(t('dingtalkDocs.authFailed'))
      setAuthPolling(false)
    }
  }, [authPolling, completeAuth, t])

  useEffect(() => {
    if (!authSessionId || !authPolling) return

    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | null = null

    const poll = async () => {
      try {
        const result = await dingtalkDocApi.getDeviceLoginStatus(authSessionId)
        if (cancelled) return

        if (result.is_authenticated) {
          await completeAuth()
          return
        }

        if (['error', 'timeout', 'cancelled'].includes(result.auth_status)) {
          authStartingRef.current = false
          setAuthError(
            result.auth_status === 'timeout'
              ? t('dingtalkDocs.authTimeout')
              : result.error || t('dingtalkDocs.authFailed')
          )
          setAuthPolling(false)
          return
        }

        timer = setTimeout(poll, 2000)
      } catch (err) {
        console.error('Failed to poll DingTalk DWS device login:', err)
        if (!cancelled) {
          authStartingRef.current = false
          setAuthError(t('dingtalkDocs.authFailed'))
          setAuthPolling(false)
        }
      }
    }

    timer = setTimeout(poll, 2000)
    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
  }, [authPolling, authSessionId, completeAuth, t])

  const activeSectionState = {
    'my-docs': {
      nodes,
      loading,
      syncing,
      error,
      configured: isConfigured,
      lastSyncedAt,
      sync: syncDocs,
      retry: fetchDocs,
      notAuthorizedLabel: t('dingtalkDocs.notAuthorized'),
    },
    'team-files': {
      nodes: teamFileNodes,
      loading: teamFileLoading,
      syncing: teamFileSyncing,
      error: teamFileError,
      configured: teamFileConfigured,
      lastSyncedAt: teamFileLastSyncedAt,
      sync: syncTeamFiles,
      retry: fetchTeamFiles,
      notAuthorizedLabel: t('dingtalkDocs.teamFilesNotAuthorized'),
    },
    wikispace: {
      nodes: wikispaceNodes,
      loading: wikispaceLoading,
      syncing: wikispaceSyncing,
      error: wikispaceError,
      configured: wikispaceConfigured,
      lastSyncedAt: wikispaceLastSyncedAt,
      sync: syncWikispace,
      retry: fetchWikispace,
      notAuthorizedLabel: t('dingtalkDocs.wikispaceNotAuthorized'),
    },
  }[activeSection]
  const activeNodes = activeSectionState.nodes
  const visibleActiveNodes = useMemo(
    () => filterDingTalkNodes(activeNodes, searchQuery),
    [activeNodes, searchQuery]
  )
  const activeLoading = activeSectionState.loading
  const activeSyncing = activeSectionState.syncing
  const activeError = activeSectionState.error
  const activeLastSyncedAt = activeSectionState.lastSyncedAt
  const handleActiveSync = activeSectionState.sync
  const handleRetry = activeSectionState.retry

  /** Render the content area for the active section. */
  const renderContent = () => {
    if (activeLoading) {
      return (
        <div className="py-6 px-4 text-center text-sm text-text-muted">
          {t('common:actions.loading')}
        </div>
      )
    }

    if (!isAuthenticated) {
      return (
        <div className="py-5 px-5 text-center space-y-3" data-testid="dingtalk-auth-panel">
          <div className="mx-auto flex h-9 w-9 items-center justify-center rounded-full bg-primary/10 text-primary">
            <KeyRound className="h-4 w-4" />
          </div>
          <div className="space-y-1">
            <p className="text-sm font-medium text-text-primary">{t('dingtalkDocs.authTitle')}</p>
            <p className="text-xs leading-5 text-text-muted">{t('dingtalkDocs.authDescription')}</p>
          </div>
          <button
            type="button"
            onClick={handleStartAuth}
            disabled={authPolling}
            className="inline-flex items-center justify-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 transition-colors disabled:opacity-50"
            data-testid="dingtalk-start-auth-button"
          >
            {authPolling ? t('dingtalkDocs.authorizing') : t('dingtalkDocs.authorize')}
            <ExternalLink className="w-3.5 h-3.5" />
          </button>
          {userCode && (
            <div className="rounded-md border border-border bg-surface px-3 py-2 text-left">
              <div className="text-xs text-text-muted">{t('dingtalkDocs.userCode')}</div>
              <div className="mt-1 font-mono text-sm text-text-primary">{userCode}</div>
            </div>
          )}
          {verificationUrl && (
            <a
              href={verificationUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
              data-testid="dingtalk-auth-link"
            >
              {t('dingtalkDocs.openAuthLink')}
              <ExternalLink className="h-3 w-3" />
            </a>
          )}
          {authPolling && (
            <p className="text-xs text-text-muted">{t('dingtalkDocs.waitingAuth')}</p>
          )}
          {authError && <p className="text-xs text-red-500">{authError}</p>}
          {authStatus === 'error' && !authError && (
            <p className="text-xs text-red-500">{t('dingtalkDocs.authFailed')}</p>
          )}
        </div>
      )
    }

    if (activeError) {
      return (
        <div className="py-4 px-3 text-center space-y-2">
          <p className="text-sm text-red-500">{activeError}</p>
          <button
            type="button"
            onClick={handleRetry}
            className="text-xs text-primary hover:underline"
            data-testid="dingtalk-retry-button"
          >
            {t('common:actions.retry')}
          </button>
        </div>
      )
    }

    if (!activeSectionState.configured) {
      return (
        <div className="py-6 px-4 text-center space-y-3">
          <p className="text-sm text-text-muted">{activeSectionState.notAuthorizedLabel}</p>
        </div>
      )
    }

    if (activeNodes.length === 0) {
      return (
        <div className="py-6 px-4 text-center space-y-3">
          <p className="text-sm text-text-muted">{t('dingtalkDocs.empty')}</p>
          <button
            type="button"
            onClick={handleActiveSync}
            disabled={activeSyncing}
            className="inline-flex items-center gap-1.5 text-sm text-primary hover:text-primary/80 font-medium transition-colors disabled:opacity-50"
            data-testid="dingtalk-empty-sync-button"
          >
            <RefreshCw className={cn('w-3.5 h-3.5', activeSyncing && 'animate-spin')} />
            {activeSyncing ? t('dingtalkDocs.syncing') : t('dingtalkDocs.syncNow')}
          </button>
        </div>
      )
    }

    return visibleActiveNodes.map(node => (
      <DingtalkContextTreeNode
        key={getDingTalkSelectionKey(node.source, node.dingtalk_node_id)}
        node={node}
        level={0}
        selectedIds={selectedContexts}
        onToggle={handleToggle}
        searchQuery={searchQuery}
      />
    ))
  }

  return (
    <div className="flex flex-col min-h-0 flex-1">
      {/* Section switcher - always visible */}
      <div className="flex border-b border-border flex-shrink-0">
        <button
          type="button"
          onClick={() => setActiveSection('my-docs')}
          className={cn(
            'flex-1 py-1.5 text-xs font-medium transition-colors',
            activeSection === 'my-docs'
              ? 'text-text-primary border-b-2 border-primary'
              : 'text-text-muted hover:text-text-primary'
          )}
          data-testid="dingtalk-section-my-docs"
        >
          {t('dingtalkDocs.myDocsTab')}
        </button>
        <button
          type="button"
          onClick={() => setActiveSection('team-files')}
          className={cn(
            'flex-1 py-1.5 text-xs font-medium transition-colors',
            activeSection === 'team-files'
              ? 'text-text-primary border-b-2 border-primary'
              : 'text-text-muted hover:text-text-primary'
          )}
          data-testid="dingtalk-section-team-files"
        >
          {t('dingtalkDocs.teamFilesTab')}
        </button>
        <button
          type="button"
          onClick={() => setActiveSection('wikispace')}
          className={cn(
            'flex-1 py-1.5 text-xs font-medium transition-colors',
            activeSection === 'wikispace'
              ? 'text-text-primary border-b-2 border-primary'
              : 'text-text-muted hover:text-text-primary'
          )}
          data-testid="dingtalk-section-wikispace"
        >
          {t('dingtalkDocs.wikispaceTab')}
        </button>
      </div>

      {/* Search input */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-border flex-shrink-0">
        <Search className="w-3.5 h-3.5 text-text-muted flex-shrink-0" />
        <input
          type="text"
          value={searchQuery}
          onChange={e => setSearchQuery(e.target.value)}
          placeholder={t('dingtalkDocs.searchPlaceholder')}
          className="flex-1 text-sm bg-transparent outline-none text-text-primary placeholder:text-text-muted"
          data-testid="dingtalk-search-input"
        />
      </div>

      {/* Sync toolbar */}
      <div className="flex items-center justify-between px-3 py-1.5 border-b border-border flex-shrink-0">
        <span className="text-xs text-text-muted">
          {activeLastSyncedAt
            ? t('dingtalkDocs.lastSynced', {
                time: new Date(activeLastSyncedAt).toLocaleString(),
              })
            : t('dingtalkDocs.neverSynced')}
          {selectedDocCount > 0 && (
            <span className="ml-2 text-primary font-medium">
              {t('dingtalkDocs.selectedCount', { count: selectedDocCount })}
            </span>
          )}
        </span>
        <button
          type="button"
          onClick={handleActiveSync}
          disabled={activeSyncing}
          className={cn(
            'flex items-center gap-1 text-xs text-primary hover:text-primary/80 transition-colors',
            activeSyncing && 'opacity-50 cursor-not-allowed'
          )}
          data-testid="dingtalk-sync-button"
        >
          <RefreshCw className={cn('w-3 h-3', activeSyncing && 'animate-spin')} />
          {activeSyncing ? t('dingtalkDocs.syncing') : t('dingtalkDocs.sync')}
        </button>
      </div>

      {selectionError && (
        <div className="border-b border-border px-3 py-1.5 text-xs text-red-500">
          {selectionError}
        </div>
      )}

      {/* Tree content area */}
      <div className="overflow-y-auto flex-1 max-h-[260px] py-1 px-1">{renderContent()}</div>
    </div>
  )
}
