// SPDX-FileCopyrightText: 2026 Weibo, Inc.
//
// SPDX-License-Identifier: Apache-2.0

'use client'

import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import {
  AlertCircle,
  CheckCircle2,
  GitCommit,
  Loader2,
  PackageCheck,
  RefreshCw,
} from 'lucide-react'

import {
  pluginRepositoryApis,
  type GitRefKind,
  type PluginCandidate,
  type PluginPublication,
  type PluginRepository,
  type PluginRepositoryInspection,
  type PluginRepositoryRef,
} from '@/apis/pluginRepositories'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import TopNavigation from '@/features/layout/TopNavigation'
import UserMenu from '@/features/layout/UserMenu'
import { useToast } from '@/hooks/use-toast'
import { useTranslation } from '@/hooks/useTranslation'

const terminalStatuses = new Set(['published', 'failed'])

export default function DeveloperPluginsPage() {
  const { t } = useTranslation('developer')
  const { toast } = useToast()
  const [repositories, setRepositories] = useState<PluginRepository[]>([])
  const [repositoryId, setRepositoryId] = useState<number | null>(null)
  const [refs, setRefs] = useState<PluginRepositoryRef[]>([])
  const [selectedRef, setSelectedRef] = useState('')
  const [selectedKind, setSelectedKind] = useState<GitRefKind>('branch')
  const [inspection, setInspection] = useState<PluginRepositoryInspection | null>(null)
  const [publications, setPublications] = useState<PluginPublication[]>([])
  const [confirmCandidate, setConfirmCandidate] = useState<PluginCandidate | null>(null)
  const [loading, setLoading] = useState(true)
  const [checking, setChecking] = useState(false)
  const [publishing, setPublishing] = useState(false)

  const repository = useMemo(
    () => repositories.find(item => item.id === repositoryId) || null,
    [repositories, repositoryId]
  )
  const canPublish =
    repository?.myRole === 'Owner' ||
    repository?.myRole === 'Maintainer' ||
    repository?.myRole === 'Developer'

  useEffect(() => {
    const load = async () => {
      try {
        const response = await pluginRepositoryApis.listAccessible()
        setRepositories(response.items)
        setRepositoryId(response.items[0]?.id || null)
      } catch {
        setRepositories([])
      } finally {
        setLoading(false)
      }
    }
    void load()
  }, [])

  const loadRepository = useCallback(async () => {
    if (!repositoryId) return
    setInspection(null)
    try {
      const [refResponse, publicationResponse] = await Promise.all([
        pluginRepositoryApis.listRefs(repositoryId),
        pluginRepositoryApis.listPublications(repositoryId),
      ])
      setRefs(refResponse.items)
      setPublications(publicationResponse.items)
      const preferred =
        refResponse.items.find(
          item => item.name === repository?.defaultRef && item.kind === 'branch'
        ) || refResponse.items[0]
      setSelectedRef(preferred?.name || '')
      setSelectedKind(preferred?.kind || 'branch')
    } catch (error) {
      toast({
        title: t('load_failed'),
        description: error instanceof Error ? error.message : undefined,
        variant: 'destructive',
      })
    }
  }, [repository?.defaultRef, repositoryId, t, toast])

  useEffect(() => {
    void loadRepository()
  }, [loadRepository])

  useEffect(() => {
    const active = publications.filter(item => !terminalStatuses.has(item.status))
    if (active.length === 0) return
    const timer = window.setInterval(async () => {
      const updates = await Promise.all(
        active.map(item => pluginRepositoryApis.getPublication(item.id))
      )
      setPublications(current =>
        current.map(item => updates.find(update => update.id === item.id) || item)
      )
    }, 2000)
    return () => window.clearInterval(timer)
  }, [publications])

  const inspect = async () => {
    if (!repositoryId || !selectedRef) return
    setChecking(true)
    try {
      setInspection(await pluginRepositoryApis.inspect(repositoryId, selectedRef, selectedKind))
    } catch (error) {
      toast({
        title: t('inspect_failed'),
        description: error instanceof Error ? error.message : undefined,
        variant: 'destructive',
      })
    } finally {
      setChecking(false)
    }
  }

  const publish = async () => {
    if (!repositoryId || !inspection || !confirmCandidate) return
    setPublishing(true)
    try {
      const publication = await pluginRepositoryApis.publish(repositoryId, {
        slug: confirmCandidate.slug,
        ref: inspection.requestedRef,
        kind: inspection.refKind,
        expectedCommitSha: inspection.resolvedCommitSha,
      })
      setPublications(current => [publication, ...current])
      setConfirmCandidate(null)
      toast({ title: t('publication_queued') })
    } catch (error) {
      toast({
        title: t('publish_failed'),
        description: error instanceof Error ? error.message : undefined,
        variant: 'destructive',
      })
    } finally {
      setPublishing(false)
    }
  }

  const retry = async (publicationId: number) => {
    try {
      const updated = await pluginRepositoryApis.retryPublication(publicationId)
      setPublications(current => current.map(item => (item.id === updated.id ? updated : item)))
    } catch (error) {
      toast({
        title: t('retry_failed'),
        description: error instanceof Error ? error.message : undefined,
        variant: 'destructive',
      })
    }
  }

  return (
    <div className="min-h-screen bg-base" data-testid="developer-plugin-page">
      <TopNavigation title={t('title')} showLogo>
        <UserMenu />
      </TopNavigation>
      <main className="mx-auto w-full max-w-6xl space-y-5 px-4 py-6 sm:px-6">
        <div>
          <h1 className="text-2xl font-semibold text-text-primary">{t('title')}</h1>
          <p className="mt-1 text-sm text-text-muted">{t('description')}</p>
        </div>

        {loading ? (
          <div className="flex min-h-64 items-center justify-center">
            <Loader2 className="h-6 w-6 animate-spin" aria-hidden />
          </div>
        ) : repositories.length === 0 ? (
          <Card className="flex min-h-64 flex-col items-center justify-center gap-3 p-8 text-center">
            <AlertCircle className="h-8 w-8 text-text-muted" aria-hidden />
            <h2 className="font-medium text-text-primary">{t('access_denied')}</h2>
            <p className="max-w-md text-sm text-text-muted">{t('access_denied_description')}</p>
          </Card>
        ) : (
          <>
            <Card className="grid gap-4 p-5 md:grid-cols-[1fr_1fr_auto] md:items-end">
              <Field label={t('repository')}>
                <select
                  className="h-10 w-full rounded-md border border-border bg-surface px-3 text-sm"
                  value={repositoryId || ''}
                  onChange={event => setRepositoryId(Number(event.target.value))}
                  data-testid="developer-plugin-repository-select"
                >
                  {repositories.map(item => (
                    <option key={item.id} value={item.id}>
                      {item.name} · {item.visibility}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label={t('ref')}>
                <select
                  className="h-10 w-full rounded-md border border-border bg-surface px-3 text-sm"
                  value={`${selectedKind}:${selectedRef}`}
                  onChange={event => {
                    const [kind, ...name] = event.target.value.split(':')
                    setSelectedKind(kind as GitRefKind)
                    setSelectedRef(name.join(':'))
                    setInspection(null)
                  }}
                  data-testid="developer-plugin-ref-select"
                >
                  {refs.map(item => (
                    <option key={`${item.kind}:${item.name}`} value={`${item.kind}:${item.name}`}>
                      {item.kind} · {item.name}
                    </option>
                  ))}
                </select>
              </Field>
              <Button
                onClick={() => void inspect()}
                disabled={!selectedRef || checking}
                data-testid="developer-plugin-inspect"
              >
                {checking ? <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden /> : null}
                {t('inspect')}
              </Button>
            </Card>

            {inspection ? (
              <Card className="space-y-4 p-5" data-testid="developer-plugin-candidates">
                <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                  <div>
                    <h2 className="font-semibold text-text-primary">{t('candidates')}</h2>
                    <p className="font-mono text-xs text-text-muted">
                      {inspection.resolvedCommitSha}
                    </p>
                  </div>
                  <span className="text-xs text-text-muted">
                    {repository?.visibility === 'public'
                      ? t('public_market')
                      : t('workspace_market')}
                  </span>
                </div>
                <div className="divide-y divide-border">
                  {inspection.candidates.map(candidate => (
                    <div
                      key={candidate.slug}
                      className="flex flex-col gap-3 py-4 sm:flex-row sm:items-center sm:justify-between"
                      data-testid={`developer-plugin-candidate-${candidate.slug}`}
                    >
                      <div>
                        <p className="font-medium text-text-primary">{candidate.displayName}</p>
                        <p className="text-sm text-text-muted">
                          {candidate.slug} · {candidate.version}
                          {candidate.currentVersion
                            ? ` · ${t('current_version', { version: candidate.currentVersion })}`
                            : ''}
                        </p>
                        {candidate.blockedReason ? (
                          <p className="mt-1 text-xs text-destructive">{candidate.blockedReason}</p>
                        ) : null}
                      </div>
                      <Button
                        disabled={!candidate.publishable || !canPublish}
                        onClick={() => setConfirmCandidate(candidate)}
                        data-testid={`developer-plugin-publish-${candidate.slug}`}
                      >
                        <PackageCheck className="mr-2 h-4 w-4" aria-hidden />
                        {t('publish')}
                      </Button>
                    </div>
                  ))}
                </div>
              </Card>
            ) : null}

            <Card className="space-y-4 p-5" data-testid="developer-plugin-publications">
              <div className="flex items-center justify-between">
                <h2 className="font-semibold text-text-primary">{t('publication_history')}</h2>
                <Button variant="ghost" size="sm" onClick={() => void loadRepository()}>
                  <RefreshCw className="mr-2 h-4 w-4" aria-hidden />
                  {t('refresh')}
                </Button>
              </div>
              {publications.length === 0 ? (
                <p className="text-sm text-text-muted">{t('no_publications')}</p>
              ) : (
                <div className="divide-y divide-border">
                  {publications.map(item => (
                    <div key={item.id} className="flex items-start justify-between gap-4 py-3">
                      <div className="min-w-0">
                        <p className="font-medium text-text-primary">
                          {item.pluginSlug} · {item.version}
                        </p>
                        <p className="truncate font-mono text-xs text-text-muted">
                          {item.commitSha}
                        </p>
                        {item.errorMessage ? (
                          <p className="mt-1 text-xs text-destructive">{item.errorMessage}</p>
                        ) : null}
                      </div>
                      <div className="flex shrink-0 items-center gap-2">
                        <span className="flex items-center gap-1 text-sm text-text-secondary">
                          {item.status === 'published' ? (
                            <CheckCircle2 className="h-4 w-4 text-success" aria-hidden />
                          ) : !terminalStatuses.has(item.status) ? (
                            <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                          ) : null}
                          {t(`status.${item.status}`)}
                        </span>
                        {item.status === 'failed' && canPublish ? (
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => void retry(item.id)}
                            data-testid={`developer-plugin-retry-${item.id}`}
                          >
                            {t('retry')}
                          </Button>
                        ) : null}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </Card>
          </>
        )}
      </main>

      <Dialog
        open={confirmCandidate !== null}
        onOpenChange={open => !open && setConfirmCandidate(null)}
      >
        <DialogContent className="max-w-xl" data-testid="developer-plugin-publish-confirmation">
          <DialogHeader>
            <DialogTitle>{t('confirm_title')}</DialogTitle>
            <DialogDescription>{t('confirm_description')}</DialogDescription>
          </DialogHeader>
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
            <dt className="text-text-muted">{t('repository')}</dt>
            <dd>{repository?.name}</dd>
            <dt className="text-text-muted">{t('scope')}</dt>
            <dd>{repository?.visibility}</dd>
            <dt className="text-text-muted">{t('ref')}</dt>
            <dd>{inspection?.requestedRef}</dd>
            <dt className="text-text-muted">SHA</dt>
            <dd className="break-all font-mono text-xs">{inspection?.resolvedCommitSha}</dd>
            <dt className="text-text-muted">Slug</dt>
            <dd>{confirmCandidate?.slug}</dd>
            <dt className="text-text-muted">SemVer</dt>
            <dd>{confirmCandidate?.version}</dd>
          </dl>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirmCandidate(null)}>
              {t('cancel')}
            </Button>
            <Button
              onClick={() => void publish()}
              disabled={publishing}
              data-testid="developer-plugin-publish-confirm"
            >
              {publishing ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden />
              ) : (
                <GitCommit className="mr-2 h-4 w-4" aria-hidden />
              )}
              {t('confirm_publish')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="space-y-1.5 text-sm text-text-secondary">
      <span>{label}</span>
      {children}
    </label>
  )
}
