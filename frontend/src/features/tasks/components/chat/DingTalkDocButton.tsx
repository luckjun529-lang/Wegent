// SPDX-FileCopyrightText: 2026 Weibo, Inc.
//
// SPDX-License-Identifier: Apache-2.0

'use client'

import React, { useCallback, useState, type Dispatch, type SetStateAction } from 'react'
import { FileText } from 'lucide-react'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import { useTranslation } from '@/hooks/useTranslation'
import type { ContextItem } from '@/types/context'
import { DingTalkDocContextSelector, getDingTalkSelectedIds } from './DingTalkDocContextSelector'

interface DingTalkDocButtonProps {
  selectedContexts: ContextItem[]
  onContextsChange: Dispatch<SetStateAction<ContextItem[]>>
  triggerVariant?: 'button' | 'menu-item'
}

export default function DingTalkDocButton({
  selectedContexts,
  onContextsChange,
  triggerVariant = 'button',
}: DingTalkDocButtonProps) {
  const { t } = useTranslation('chat')
  const [open, setOpen] = useState(false)
  const selectedDingTalkCount = selectedContexts.filter(ctx => ctx.type === 'dingtalk_doc').length
  const selectedIds = getDingTalkSelectedIds(selectedContexts)

  const handleSelect = useCallback(
    (context: ContextItem) => {
      onContextsChange(prev => {
        if (prev.some(ctx => ctx.id === context.id)) return prev
        return [...prev, context]
      })
    },
    [onContextsChange]
  )

  const handleDeselect = useCallback(
    (id: string) => {
      onContextsChange(prev => prev.filter(ctx => ctx.id !== id))
    },
    [onContextsChange]
  )

  const handleSelectMultiple = useCallback(
    (contextsToAdd: ContextItem[]) => {
      onContextsChange(prev => {
        const existingIds = new Set(prev.map(ctx => ctx.id))
        const nextContexts = contextsToAdd.filter(ctx => !existingIds.has(ctx.id))
        return nextContexts.length > 0 ? [...prev, ...nextContexts] : prev
      })
    },
    [onContextsChange]
  )

  const handleDeselectMultiple = useCallback(
    (ids: string[]) => {
      const idSet = new Set(ids)
      onContextsChange(prev => prev.filter(ctx => !idSet.has(String(ctx.id))))
    },
    [onContextsChange]
  )

  return (
    <Popover open={open} onOpenChange={setOpen}>
      {triggerVariant === 'menu-item' ? (
        <PopoverTrigger asChild>
          <button
            type="button"
            className="w-full flex items-center justify-between px-3 py-2.5 text-left transition-colors hover:bg-hover active:bg-hover"
            data-testid="dingtalk-doc-menu-button"
          >
            <span className="flex items-center gap-3">
              <FileText className="h-4 w-4 text-text-muted" />
              <span className="text-sm">{t('dingtalkDocs.tabTitle')}</span>
            </span>
            {selectedDingTalkCount > 0 && (
              <span className="h-5 min-w-5 rounded-full bg-primary px-1.5 text-[11px] leading-5 text-white text-center">
                {selectedDingTalkCount}
              </span>
            )}
          </button>
        </PopoverTrigger>
      ) : (
        <TooltipProvider>
          <Tooltip>
            <TooltipTrigger asChild>
              <PopoverTrigger asChild>
                <button
                  type="button"
                  className="relative h-9 w-9 rounded-full flex-shrink-0 inline-flex items-center justify-center text-text-muted transition-colors hover:bg-hover hover:text-text-primary disabled:opacity-50"
                  title={t('dingtalkDocs.tabTitle')}
                  data-testid="dingtalk-doc-context-button"
                >
                  <FileText className="h-4 w-4" />
                  {selectedDingTalkCount > 0 && (
                    <span className="absolute -top-1.5 -right-1.5 h-[18px] min-w-[18px] rounded-full bg-primary px-1 text-center text-[10px] leading-[18px] text-white pointer-events-none">
                      {selectedDingTalkCount}
                    </span>
                  )}
                </button>
              </PopoverTrigger>
            </TooltipTrigger>
            <TooltipContent side="top">
              <p>{t('dingtalkDocs.tabTitle')}</p>
            </TooltipContent>
          </Tooltip>
        </TooltipProvider>
      )}
      <PopoverContent
        className="p-0 w-[420px] max-w-[calc(100vw-24px)] border border-border bg-base shadow-xl rounded-xl overflow-hidden flex flex-col"
        align="start"
        side="top"
        sideOffset={4}
        collisionPadding={8}
        data-testid="dingtalk-doc-popover"
      >
        <DingTalkDocContextSelector
          selectedContexts={selectedIds}
          onSelect={handleSelect}
          onDeselect={handleDeselect}
          onSelectMultiple={handleSelectMultiple}
          onDeselectMultiple={handleDeselectMultiple}
        />
      </PopoverContent>
    </Popover>
  )
}
