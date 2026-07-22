// SPDX-FileCopyrightText: 2026 Weibo, Inc.
//
// SPDX-License-Identifier: Apache-2.0

import enChat from '@/i18n/locales/en/chat.json'
import zhChat from '@/i18n/locales/zh-CN/chat.json'

describe('DingTalk team-file translations', () => {
  it('uses the localized Team Files tab name', () => {
    expect(zhChat.dingtalkDocs.teamFilesTab).toBe('团队文件')
    expect(enChat.dingtalkDocs.teamFilesTab).toBe('Team Files')
  })
})
