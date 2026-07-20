// SPDX-FileCopyrightText: 2026 Weibo, Inc.
//
// SPDX-License-Identifier: Apache-2.0

import '@testing-library/jest-dom'
import React from 'react'
import { fireEvent, render, screen } from '@testing-library/react'

import { ContextBadgeList } from '@/features/tasks/components/message/ContextBadgeList'
import type { SubtaskContextBrief } from '@/types/api'

jest.mock('@/hooks/useTranslation', () => ({
  useTranslation: () => ({
    t: (key: string) => {
      const labels: Record<string, string> = {
        'dingtalkDocs.docBadgeHint': 'DingTalk Doc',
        'dingtalkDocs.myDocsTab': 'My Documents',
        'dingtalkDocs.wikispaceTab': 'Knowledge Base',
      }
      return labels[key] ?? key
    },
  }),
}))

describe('ContextBadgeList', () => {
  it('renders DingTalk document attachments with a DingTalk-specific card', () => {
    const openSpy = jest.spyOn(window, 'open').mockImplementation(() => null)
    const contexts: SubtaskContextBrief[] = [
      {
        id: -1,
        context_type: 'attachment',
        name: 'Project Plan.md',
        status: 'ready',
        file_extension: '.md',
        mime_type: 'text/markdown',
        source: 'dingtalk_doc',
        dingtalk_node_id: 'node-1',
        doc_url: 'https://alidocs.dingtalk.com/i/nodes/node-1',
        dingtalk_source: 'wikispace',
      },
    ]

    render(<ContextBadgeList contexts={contexts} />)

    const badge = screen.getByTestId('message-context-dingtalk-doc')
    expect(badge).toHaveTextContent('Project Plan.md')
    expect(badge).toHaveTextContent('DingTalk Doc · Knowledge Base')

    fireEvent.click(badge)
    expect(openSpy).toHaveBeenCalledWith(
      'https://alidocs.dingtalk.com/i/nodes/node-1',
      '_blank',
      'noopener,noreferrer'
    )

    openSpy.mockRestore()
  })
})
