// SPDX-FileCopyrightText: 2026 Weibo, Inc.
//
// SPDX-License-Identifier: Apache-2.0

import { render, screen, waitFor } from '@testing-library/react'

import PluginRepositoryManagement from '@/features/admin/components/PluginRepositoryManagement'
import { pluginRepositoryApis } from '@/apis/pluginRepositories'

jest.mock('@/apis/pluginRepositories', () => ({
  pluginRepositoryApis: {
    listAdmin: jest.fn(),
    create: jest.fn(),
    update: jest.fn(),
    validate: jest.fn(),
    listMembers: jest.fn(),
    replaceMembers: jest.fn(),
  },
}))

jest.mock('@/hooks/useTranslation', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

jest.mock('@/hooks/use-toast', () => ({
  useToast: () => ({ toast: jest.fn() }),
}))

describe('PluginRepositoryManagement', () => {
  it('renders configured repositories without exposing credentials', async () => {
    jest.mocked(pluginRepositoryApis.listAdmin).mockResolvedValue({
      items: [
        {
          id: 7,
          name: 'Official plugins',
          provider: 'github',
          repositoryUrl: 'https://github.com/wecode-ai/wework-plugins',
          visibility: 'public',
          defaultRef: 'main',
          marketplacePath: '.agents/plugins/marketplace.json',
          allowedBranchPatterns: ['main'],
          allowedTagPatterns: ['v*'],
          hasCredential: true,
          isInternal: false,
          isEnabled: true,
          myRole: 'Owner',
          lastValidatedAt: '2026-08-20T00:00:00',
          lastError: null,
          createdAt: '2026-08-20T00:00:00',
          updatedAt: '2026-08-20T00:00:00',
        },
      ],
    })

    render(<PluginRepositoryManagement />)

    await waitFor(() => expect(screen.getByText('Official plugins')).toBeInTheDocument())
    expect(screen.getByText('plugin_repositories.credential_configured')).toBeInTheDocument()
    expect(screen.queryByText(/token/i)).not.toBeInTheDocument()
    expect(screen.getByTestId('plugin-repository-validate-7')).toBeInTheDocument()
    expect(screen.getByTestId('plugin-repository-members-7')).toBeInTheDocument()
  })
})
