// SPDX-FileCopyrightText: 2025 Weibo, Inc.
//
// SPDX-License-Identifier: Apache-2.0

/**
 * DingTalk synced document node types.
 */

export type DingtalkNodeSource = 'docs' | 'wikispace' | 'team_files'

export interface DingtalkDocNode {
  id: number
  dingtalk_node_id: string
  name: string
  doc_url: string
  parent_node_id: string
  node_type: 'folder' | 'doc' | 'file'
  workspace_id: string
  content_type: string
  content_updated_at: string
  source: DingtalkNodeSource
  is_active: boolean
  last_synced_at: string
  created_at: string
  updated_at: string
  children?: DingtalkDocNode[]
}

export interface DingtalkDocTreeResponse {
  nodes: DingtalkDocNode[]
  total_count: number
}

export interface DingtalkSyncStatus {
  last_synced_at: string | null
  total_nodes: number
  is_configured: boolean
  is_authenticated?: boolean | null
  auth_status?: string | null
}

export interface DingtalkSyncResult {
  added: number
  updated: number
  deleted: number
  total: number
  sync_time: string
  dws_nodes_fetched?: number
  truncated?: boolean
}

export type DingtalkDwsAuthStatus =
  | 'authenticated'
  | 'unauthenticated'
  | 'pending'
  | 'error'
  | 'timeout'
  | 'cancelled'

export interface DingtalkDwsAuthStatusResponse {
  is_authenticated: boolean
  auth_status: DingtalkDwsAuthStatus
  error?: string | null
}

export interface DingtalkDwsDeviceLoginResponse extends DingtalkDwsAuthStatusResponse {
  verification_url?: string | null
  user_code?: string | null
  session_id?: string | null
}
