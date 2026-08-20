// SPDX-FileCopyrightText: 2026 Weibo, Inc.
//
// SPDX-License-Identifier: Apache-2.0

import { useEffect, useState } from 'react'

import { pluginRepositoryApis } from '@/apis/pluginRepositories'

export function usePluginPublisherAccess(userRole?: string) {
  const [hasAccess, setHasAccess] = useState(userRole === 'admin')

  useEffect(() => {
    if (!userRole) {
      setHasAccess(false)
      return
    }
    if (userRole === 'admin') {
      setHasAccess(true)
      return
    }
    let active = true
    pluginRepositoryApis
      .listAccessible()
      .then(response => active && setHasAccess(response.items.length > 0))
      .catch(() => active && setHasAccess(false))
    return () => {
      active = false
    }
  }, [userRole])

  return hasAccess
}
