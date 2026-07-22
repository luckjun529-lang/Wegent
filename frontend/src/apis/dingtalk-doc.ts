// SPDX-FileCopyrightText: 2025 Weibo, Inc.
//
// SPDX-License-Identifier: Apache-2.0

/**
 * DingTalk document API functions.
 */

import client from './client'
import type {
  DingtalkDwsAuthStatusResponse,
  DingtalkDwsDeviceLoginResponse,
  DingtalkDocTreeResponse,
  DingtalkSyncStatus,
  DingtalkSyncResult,
} from '@/types/dingtalk-doc'

export const dingtalkDocApi = {
  /**
   * Get DingTalk DWS authorization status for the current user.
   */
  getAuthStatus: async (): Promise<DingtalkDwsAuthStatusResponse> => {
    return client.get<DingtalkDwsAuthStatusResponse>('/dingtalk-dws/auth/status')
  },

  /**
   * Start headless DingTalk device-code authorization.
   */
  startDeviceLogin: async (): Promise<DingtalkDwsDeviceLoginResponse> => {
    return client.post<DingtalkDwsDeviceLoginResponse>('/dingtalk-dws/auth/device-login')
  },

  /**
   * Poll a DingTalk device-code authorization session.
   */
  getDeviceLoginStatus: async (sessionId: string): Promise<DingtalkDwsDeviceLoginResponse> => {
    return client.get<DingtalkDwsDeviceLoginResponse>(
      `/dingtalk-dws/auth/device-login/${sessionId}`
    )
  },

  /**
   * Get all synced DingTalk document nodes as a tree structure.
   */
  getDocs: async (): Promise<DingtalkDocTreeResponse> => {
    return client.get<DingtalkDocTreeResponse>('/dingtalk-docs')
  },

  /**
   * Trigger sync of DingTalk documents through backend DWS.
   */
  syncDocs: async (): Promise<DingtalkSyncResult> => {
    return client.post<DingtalkSyncResult>('/dingtalk-docs/sync')
  },

  /**
   * Get the sync status for the current user.
   */
  getSyncStatus: async (): Promise<DingtalkSyncStatus> => {
    return client.get<DingtalkSyncStatus>('/dingtalk-docs/sync-status')
  },

  /**
   * Delete a synced document node from local cache.
   */
  deleteDoc: async (nodeId: number): Promise<void> => {
    await client.delete(`/dingtalk-docs/${nodeId}`)
  },

  /**
   * Get all synced DingTalk wikispace nodes as a tree structure.
   */
  getWikispaceNodes: async (): Promise<DingtalkDocTreeResponse> => {
    return client.get<DingtalkDocTreeResponse>('/dingtalk-wikispace')
  },

  /**
   * Trigger sync of DingTalk wikispace nodes through backend DWS.
   */
  syncWikispaceNodes: async (): Promise<DingtalkSyncResult> => {
    return client.post<DingtalkSyncResult>('/dingtalk-wikispace/sync')
  },

  /**
   * Get the wikispace sync status for the current user.
   */
  getWikispaceSyncStatus: async (): Promise<DingtalkSyncStatus> => {
    return client.get<DingtalkSyncStatus>('/dingtalk-wikispace/sync-status')
  },

  /**
   * Get all synced DingTalk team-file nodes as a tree structure.
   */
  getTeamFiles: async (): Promise<DingtalkDocTreeResponse> => {
    return client.get<DingtalkDocTreeResponse>('/dingtalk-team-files')
  },

  /**
   * Trigger sync of DingTalk team-file nodes through backend DWS.
   */
  syncTeamFiles: async (): Promise<DingtalkSyncResult> => {
    return client.post<DingtalkSyncResult>('/dingtalk-team-files/sync')
  },

  /**
   * Get the team-file sync status for the current user.
   */
  getTeamFilesSyncStatus: async (): Promise<DingtalkSyncStatus> => {
    return client.get<DingtalkSyncStatus>('/dingtalk-team-files/sync-status')
  },
}
