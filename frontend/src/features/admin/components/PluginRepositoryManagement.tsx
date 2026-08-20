// SPDX-FileCopyrightText: 2026 Weibo, Inc.
//
// SPDX-License-Identifier: Apache-2.0

'use client'

import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { CheckCircle2, GitBranch, Loader2, Plus, Settings2, ShieldCheck, Users } from 'lucide-react'

import {
  pluginRepositoryApis,
  type PluginRepository,
  type PluginRepositoryCreate,
  type PluginRepositoryMember,
  type PluginRepositoryProvider,
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
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { useToast } from '@/hooks/use-toast'
import { useTranslation } from '@/hooks/useTranslation'

const initialForm: PluginRepositoryCreate = {
  name: '',
  provider: 'github',
  repositoryUrl: '',
  visibility: 'public',
  defaultRef: 'main',
  marketplacePath: '.agents/plugins/marketplace.json',
  allowedBranchPatterns: ['main'],
  allowedTagPatterns: ['v*'],
  credential: '',
  isInternal: false,
  isEnabled: true,
}

interface RepositoryEditForm {
  name: string
  repositoryUrl: string
  defaultRef: string
  marketplacePath: string
  branchPatterns: string
  tagPatterns: string
  credentialAction: 'keep' | 'replace' | 'remove'
  credential: string
}

export default function PluginRepositoryManagement() {
  const { t } = useTranslation('admin')
  const { toast } = useToast()
  const [items, setItems] = useState<PluginRepository[]>([])
  const [loading, setLoading] = useState(true)
  const [busyId, setBusyId] = useState<number | null>(null)
  const [createOpen, setCreateOpen] = useState(false)
  const [form, setForm] = useState<PluginRepositoryCreate>(initialForm)
  const [editingRepository, setEditingRepository] = useState<PluginRepository | null>(null)
  const [editForm, setEditForm] = useState<RepositoryEditForm | null>(null)
  const [membersRepository, setMembersRepository] = useState<PluginRepository | null>(null)
  const [members, setMembers] = useState<PluginRepositoryMember[]>([])

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setItems((await pluginRepositoryApis.listAdmin()).items)
    } catch {
      toast({ title: t('plugin_repositories.load_failed'), variant: 'destructive' })
    } finally {
      setLoading(false)
    }
  }, [t, toast])

  useEffect(() => {
    void load()
  }, [load])

  const selectProvider = (provider: PluginRepositoryProvider) => {
    const github = provider === 'github'
    setForm(current => ({
      ...current,
      provider,
      visibility: github ? 'public' : 'workspace',
      isInternal: !github,
    }))
  }

  const create = async () => {
    try {
      await pluginRepositoryApis.create(form)
      setCreateOpen(false)
      setForm(initialForm)
      await load()
      toast({ title: t('plugin_repositories.created') })
    } catch (error) {
      toast({
        title: t('plugin_repositories.create_failed'),
        description: error instanceof Error ? error.message : undefined,
        variant: 'destructive',
      })
    }
  }

  const validate = async (repository: PluginRepository) => {
    setBusyId(repository.id)
    try {
      const updated = await pluginRepositoryApis.validate(repository.id)
      setItems(current => current.map(item => (item.id === updated.id ? updated : item)))
      toast({ title: t('plugin_repositories.validated') })
    } catch (error) {
      toast({
        title: t('plugin_repositories.validate_failed'),
        description: error instanceof Error ? error.message : undefined,
        variant: 'destructive',
      })
      await load()
    } finally {
      setBusyId(null)
    }
  }

  const toggle = async (repository: PluginRepository) => {
    setBusyId(repository.id)
    try {
      const updated = await pluginRepositoryApis.update(repository.id, {
        isEnabled: !repository.isEnabled,
      })
      setItems(current => current.map(item => (item.id === updated.id ? updated : item)))
    } catch (error) {
      toast({
        title: t('plugin_repositories.update_failed'),
        description: error instanceof Error ? error.message : undefined,
        variant: 'destructive',
      })
    } finally {
      setBusyId(null)
    }
  }

  const openEdit = (repository: PluginRepository) => {
    setEditingRepository(repository)
    setEditForm({
      name: repository.name,
      repositoryUrl: repository.repositoryUrl,
      defaultRef: repository.defaultRef,
      marketplacePath: repository.marketplacePath,
      branchPatterns: repository.allowedBranchPatterns.join(','),
      tagPatterns: repository.allowedTagPatterns.join(','),
      credentialAction: 'keep',
      credential: '',
    })
  }

  const saveEdit = async () => {
    if (!editingRepository || !editForm) return
    setBusyId(editingRepository.id)
    try {
      const splitPatterns = (value: string) =>
        value
          .split(',')
          .map(pattern => pattern.trim())
          .filter(Boolean)
      const updated = await pluginRepositoryApis.update(editingRepository.id, {
        name: editForm.name,
        repositoryUrl: editForm.repositoryUrl,
        defaultRef: editForm.defaultRef,
        marketplacePath: editForm.marketplacePath,
        allowedBranchPatterns: splitPatterns(editForm.branchPatterns),
        allowedTagPatterns: splitPatterns(editForm.tagPatterns),
        credentialAction: editForm.credentialAction,
        ...(editForm.credentialAction === 'replace' ? { credential: editForm.credential } : {}),
      })
      setItems(current => current.map(item => (item.id === updated.id ? updated : item)))
      setEditingRepository(null)
      setEditForm(null)
      toast({ title: t('plugin_repositories.updated') })
    } catch (error) {
      toast({
        title: t('plugin_repositories.update_failed'),
        description: error instanceof Error ? error.message : undefined,
        variant: 'destructive',
      })
    } finally {
      setBusyId(null)
    }
  }

  const openMembers = async (repository: PluginRepository) => {
    setMembersRepository(repository)
    try {
      setMembers((await pluginRepositoryApis.listMembers(repository.id)).items)
    } catch {
      setMembers([])
    }
  }

  const saveMembers = async () => {
    if (!membersRepository) return
    try {
      await pluginRepositoryApis.replaceMembers(membersRepository.id, members)
      setMembersRepository(null)
      toast({ title: t('plugin_repositories.members_saved') })
    } catch (error) {
      toast({
        title: t('plugin_repositories.members_save_failed'),
        description: error instanceof Error ? error.message : undefined,
        variant: 'destructive',
      })
    }
  }

  return (
    <section className="space-y-4" data-testid="plugin-repository-management">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h3 className="text-lg font-semibold text-text-primary">
            {t('plugin_repositories.title')}
          </h3>
          <p className="text-sm text-text-muted">{t('plugin_repositories.description')}</p>
        </div>
        <Button
          type="button"
          onClick={() => setCreateOpen(true)}
          data-testid="plugin-repository-create-button"
        >
          <Plus className="mr-2 h-4 w-4" aria-hidden />
          {t('plugin_repositories.create')}
        </Button>
      </div>

      {loading ? (
        <div className="flex min-h-28 items-center justify-center">
          <Loader2 className="h-5 w-5 animate-spin" aria-hidden />
        </div>
      ) : items.length === 0 ? (
        <Card className="p-6 text-center text-sm text-text-muted">
          {t('plugin_repositories.empty')}
        </Card>
      ) : (
        <div className="grid gap-3 lg:grid-cols-2">
          {items.map(repository => (
            <Card
              key={repository.id}
              className="space-y-3 p-4"
              data-testid={`plugin-repository-item-${repository.id}`}
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <GitBranch className="h-4 w-4 shrink-0 text-primary" aria-hidden />
                    <h4 className="truncate font-medium text-text-primary">{repository.name}</h4>
                  </div>
                  <p className="mt-1 truncate text-xs text-text-muted">
                    {repository.repositoryUrl}
                  </p>
                </div>
                <span className="rounded-full bg-muted px-2 py-1 text-xs text-text-secondary">
                  {repository.visibility === 'public'
                    ? t('plugin_repositories.public')
                    : t('plugin_repositories.workspace')}
                </span>
              </div>
              <div className="flex flex-wrap gap-2 text-xs text-text-muted">
                <span>{repository.provider}</span>
                <span>·</span>
                <span>{repository.defaultRef}</span>
                <span>·</span>
                <span>
                  {repository.hasCredential
                    ? t('plugin_repositories.credential_configured')
                    : t('plugin_repositories.no_credential')}
                </span>
                {repository.lastValidatedAt ? (
                  <CheckCircle2 className="h-4 w-4 text-success" aria-label="validated" />
                ) : null}
              </div>
              {repository.lastError ? (
                <p className="rounded bg-destructive/10 p-2 text-xs text-destructive">
                  {repository.lastError}
                </p>
              ) : null}
              <div className="flex flex-wrap gap-2">
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={() => openEdit(repository)}
                  data-testid={`plugin-repository-edit-${repository.id}`}
                >
                  <Settings2 className="mr-1.5 h-4 w-4" aria-hidden />
                  {t('plugin_repositories.configure')}
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={busyId === repository.id}
                  onClick={() => void validate(repository)}
                  data-testid={`plugin-repository-validate-${repository.id}`}
                >
                  <ShieldCheck className="mr-1.5 h-4 w-4" aria-hidden />
                  {t('plugin_repositories.validate')}
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={() => void openMembers(repository)}
                  data-testid={`plugin-repository-members-${repository.id}`}
                >
                  <Users className="mr-1.5 h-4 w-4" aria-hidden />
                  {t('plugin_repositories.members')}
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={busyId === repository.id}
                  onClick={() => void toggle(repository)}
                  data-testid={`plugin-repository-toggle-${repository.id}`}
                >
                  {repository.isEnabled
                    ? t('plugin_repositories.disable')
                    : t('plugin_repositories.enable')}
                </Button>
              </div>
            </Card>
          ))}
        </div>
      )}

      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent className="max-w-xl" data-testid="plugin-repository-create-dialog">
          <DialogHeader>
            <DialogTitle>{t('plugin_repositories.create')}</DialogTitle>
            <DialogDescription>{t('plugin_repositories.create_description')}</DialogDescription>
          </DialogHeader>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label={t('plugin_repositories.name')}>
              <Input
                value={form.name}
                onChange={event => setForm(current => ({ ...current, name: event.target.value }))}
                data-testid="plugin-repository-name"
              />
            </Field>
            <Field label={t('plugin_repositories.provider')}>
              <select
                className="h-10 w-full rounded-md border border-border bg-surface px-3 text-sm"
                value={form.provider}
                onChange={event => selectProvider(event.target.value as PluginRepositoryProvider)}
                data-testid="plugin-repository-provider"
              >
                <option value="github">GitHub</option>
                <option value="gitlab">GitLab</option>
              </select>
            </Field>
            <div className="sm:col-span-2">
              <Field label={t('plugin_repositories.url')}>
                <Input
                  value={form.repositoryUrl}
                  onChange={event =>
                    setForm(current => ({ ...current, repositoryUrl: event.target.value }))
                  }
                  placeholder="https://github.com/org/repository"
                  data-testid="plugin-repository-url"
                />
              </Field>
            </div>
            <Field label={t('plugin_repositories.default_ref')}>
              <Input
                value={form.defaultRef}
                onChange={event =>
                  setForm(current => ({
                    ...current,
                    defaultRef: event.target.value,
                    allowedBranchPatterns: [event.target.value],
                  }))
                }
                data-testid="plugin-repository-default-ref"
              />
            </Field>
            <Field label={t('plugin_repositories.tag_patterns')}>
              <Input
                value={form.allowedTagPatterns.join(',')}
                onChange={event =>
                  setForm(current => ({
                    ...current,
                    allowedTagPatterns: event.target.value
                      .split(',')
                      .map(value => value.trim())
                      .filter(Boolean),
                  }))
                }
                data-testid="plugin-repository-tag-patterns"
              />
            </Field>
            <div className="sm:col-span-2">
              <Field label={t('plugin_repositories.credential')}>
                <Input
                  type="password"
                  value={form.credential}
                  onChange={event =>
                    setForm(current => ({ ...current, credential: event.target.value }))
                  }
                  data-testid="plugin-repository-credential"
                />
              </Field>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setCreateOpen(false)}>
              {t('common.cancel')}
            </Button>
            <Button
              onClick={() => void create()}
              disabled={!form.name.trim() || !form.repositoryUrl.trim()}
              data-testid="plugin-repository-create-submit"
            >
              {t('common.create')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={editingRepository !== null}
        onOpenChange={open => {
          if (!open) {
            setEditingRepository(null)
            setEditForm(null)
          }
        }}
      >
        <DialogContent className="max-w-xl" data-testid="plugin-repository-edit-dialog">
          <DialogHeader>
            <DialogTitle>{t('plugin_repositories.configure')}</DialogTitle>
            <DialogDescription>
              {editingRepository?.provider} · {editingRepository?.visibility}
            </DialogDescription>
          </DialogHeader>
          {editForm ? (
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label={t('plugin_repositories.name')}>
                <Input
                  value={editForm.name}
                  onChange={event =>
                    setEditForm(current =>
                      current ? { ...current, name: event.target.value } : current
                    )
                  }
                  data-testid="plugin-repository-edit-name"
                />
              </Field>
              <Field label={t('plugin_repositories.default_ref')}>
                <Input
                  value={editForm.defaultRef}
                  onChange={event =>
                    setEditForm(current =>
                      current ? { ...current, defaultRef: event.target.value } : current
                    )
                  }
                  data-testid="plugin-repository-edit-default-ref"
                />
              </Field>
              <div className="sm:col-span-2">
                <Field label={t('plugin_repositories.url')}>
                  <Input
                    value={editForm.repositoryUrl}
                    onChange={event =>
                      setEditForm(current =>
                        current ? { ...current, repositoryUrl: event.target.value } : current
                      )
                    }
                    data-testid="plugin-repository-edit-url"
                  />
                </Field>
              </div>
              <div className="sm:col-span-2">
                <Field label={t('plugin_repositories.marketplace_path')}>
                  <Input
                    value={editForm.marketplacePath}
                    onChange={event =>
                      setEditForm(current =>
                        current ? { ...current, marketplacePath: event.target.value } : current
                      )
                    }
                    data-testid="plugin-repository-edit-marketplace-path"
                  />
                </Field>
              </div>
              <Field label={t('plugin_repositories.branch_patterns')}>
                <Input
                  value={editForm.branchPatterns}
                  onChange={event =>
                    setEditForm(current =>
                      current ? { ...current, branchPatterns: event.target.value } : current
                    )
                  }
                  data-testid="plugin-repository-edit-branch-patterns"
                />
              </Field>
              <Field label={t('plugin_repositories.tag_patterns')}>
                <Input
                  value={editForm.tagPatterns}
                  onChange={event =>
                    setEditForm(current =>
                      current ? { ...current, tagPatterns: event.target.value } : current
                    )
                  }
                  data-testid="plugin-repository-edit-tag-patterns"
                />
              </Field>
              <Field label={t('plugin_repositories.credential_action')}>
                <select
                  className="h-10 w-full rounded-md border border-border bg-surface px-3 text-sm"
                  value={editForm.credentialAction}
                  onChange={event =>
                    setEditForm(current =>
                      current
                        ? {
                            ...current,
                            credentialAction: event.target
                              .value as RepositoryEditForm['credentialAction'],
                          }
                        : current
                    )
                  }
                  data-testid="plugin-repository-edit-credential-action"
                >
                  <option value="keep">{t('plugin_repositories.credential_keep')}</option>
                  <option value="replace">{t('plugin_repositories.credential_replace')}</option>
                  <option value="remove">{t('plugin_repositories.credential_remove')}</option>
                </select>
              </Field>
              {editForm.credentialAction === 'replace' ? (
                <Field label={t('plugin_repositories.credential')}>
                  <Input
                    type="password"
                    value={editForm.credential}
                    onChange={event =>
                      setEditForm(current =>
                        current ? { ...current, credential: event.target.value } : current
                      )
                    }
                    data-testid="plugin-repository-edit-credential"
                  />
                </Field>
              ) : null}
            </div>
          ) : null}
          <DialogFooter>
            <Button variant="outline" onClick={() => setEditingRepository(null)}>
              {t('common.cancel')}
            </Button>
            <Button
              onClick={() => void saveEdit()}
              disabled={
                !editForm?.name.trim() ||
                !editForm.repositoryUrl.trim() ||
                (editForm.credentialAction === 'replace' && !editForm.credential.trim())
              }
              data-testid="plugin-repository-edit-save"
            >
              {t('common.save')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={membersRepository !== null}
        onOpenChange={open => !open && setMembersRepository(null)}
      >
        <DialogContent className="max-w-2xl" data-testid="plugin-repository-members-dialog">
          <DialogHeader>
            <DialogTitle>{t('plugin_repositories.members')}</DialogTitle>
            <DialogDescription>{membersRepository?.name}</DialogDescription>
          </DialogHeader>
          <div className="max-h-96 space-y-2 overflow-y-auto">
            {members.map((member, index) => (
              <div key={`${member.entityType}-${index}`} className="grid gap-2 sm:grid-cols-4">
                <select
                  className="h-10 rounded-md border border-border bg-surface px-2 text-sm"
                  value={member.entityType}
                  onChange={event =>
                    setMembers(current =>
                      current.map((item, itemIndex) =>
                        itemIndex === index
                          ? { ...item, entityType: event.target.value as 'user' | 'namespace' }
                          : item
                      )
                    )
                  }
                  data-testid={`plugin-repository-member-type-${index}`}
                >
                  <option value="user">{t('plugin_repositories.user')}</option>
                  <option value="namespace">{t('plugin_repositories.department')}</option>
                </select>
                <Input
                  value={member.entityId}
                  placeholder={t('plugin_repositories.entity_id')}
                  onChange={event =>
                    setMembers(current =>
                      current.map((item, itemIndex) =>
                        itemIndex === index ? { ...item, entityId: event.target.value } : item
                      )
                    )
                  }
                  data-testid={`plugin-repository-member-id-${index}`}
                />
                <select
                  className="h-10 rounded-md border border-border bg-surface px-2 text-sm"
                  value={member.role}
                  onChange={event =>
                    setMembers(current =>
                      current.map((item, itemIndex) =>
                        itemIndex === index
                          ? { ...item, role: event.target.value as PluginRepositoryMember['role'] }
                          : item
                      )
                    )
                  }
                  data-testid={`plugin-repository-member-role-${index}`}
                >
                  <option value="Reporter">Reporter</option>
                  <option value="Developer">Developer</option>
                  <option value="Maintainer">Maintainer</option>
                  <option value="Owner">Owner</option>
                </select>
                <Button
                  variant="outline"
                  onClick={() => setMembers(current => current.filter((_, i) => i !== index))}
                  data-testid={`plugin-repository-member-remove-${index}`}
                >
                  {t('common.delete')}
                </Button>
              </div>
            ))}
            <Button
              variant="outline"
              onClick={() =>
                setMembers(current => [
                  ...current,
                  { entityType: 'user', entityId: '', displayName: '', role: 'Developer' },
                ])
              }
              data-testid="plugin-repository-member-add"
            >
              <Plus className="mr-2 h-4 w-4" aria-hidden />
              {t('plugin_repositories.add_member')}
            </Button>
          </div>
          <DialogFooter>
            <Button onClick={() => void saveMembers()} data-testid="plugin-repository-members-save">
              {t('common.save')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  )
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="space-y-1.5">
      <Label>{label}</Label>
      {children}
    </div>
  )
}
