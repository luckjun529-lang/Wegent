// SPDX-FileCopyrightText: 2026 Weibo, Inc.
//
// SPDX-License-Identifier: Apache-2.0

import { expect, test } from '@playwright/test'

import { REGULAR_USER } from '../config/test-users'
import { buildStorageState, getJwtExpiryMs } from '../utils/auth-state'
import { createApiClient } from '../utils/api-client'

const APP_BASE_URL = process.env.E2E_BASE_URL || 'http://localhost:3000'
const API_BASE_URL = process.env.E2E_API_URL || 'http://localhost:8000'

test.describe('Plugin repository publishing access', () => {
  test('administrator can open plugin repository management', async ({ page }) => {
    await page.goto('/admin?tab=marketplace')

    await expect(page.getByTestId('plugin-repository-management')).toBeVisible()
    await expect(page.getByTestId('plugin-repository-create-button')).toBeVisible()
  })

  test('regular user cannot configure repositories or enter the publisher', async ({
    browser,
    request,
  }) => {
    const client = createApiClient(request)
    const login = await client.login(REGULAR_USER.username, REGULAR_USER.password)
    expect(login.status).toBe(200)
    const token = login.data?.access_token
    expect(token).toBeTruthy()

    const forbidden = await request.post(`${API_BASE_URL}/api/admin/plugin-repositories`, {
      headers: {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
        Connection: 'close',
      },
      data: {
        name: 'Unauthorized repository',
        provider: 'github',
        repositoryUrl: 'https://github.com/wecode-ai/wework-plugins',
        visibility: 'public',
        defaultRef: 'main',
        marketplacePath: '.agents/plugins/marketplace.json',
        allowedBranchPatterns: ['main'],
        allowedTagPatterns: ['v*'],
        isInternal: false,
        isEnabled: true,
      },
    })
    expect(forbidden.status()).toBe(403)

    const context = await browser.newContext({
      storageState: buildStorageState(APP_BASE_URL, token!, getJwtExpiryMs(token!)),
    })
    const page = await context.newPage()
    await page.goto(`${APP_BASE_URL}/developer/plugins`)

    await expect(page.getByTestId('developer-plugin-page')).toBeVisible()
    await expect(page.getByText(/No plugin repository access|没有插件仓库权限/)).toBeVisible()
    await expect(page.getByTestId('developer-plugin-candidates')).toHaveCount(0)
    await context.close()
  })
})
