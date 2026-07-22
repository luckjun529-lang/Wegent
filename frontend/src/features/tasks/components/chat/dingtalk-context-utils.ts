// SPDX-FileCopyrightText: 2026 Weibo, Inc.
//
// SPDX-License-Identifier: Apache-2.0

import type { ContextItem, DingTalkDocContext } from '@/types/context'
import type { DingtalkDocNode, DingtalkNodeSource } from '@/types/dingtalk-doc'

export const MAX_DINGTALK_DOC_CONTEXTS = 10

export function getDingTalkSelectionKey(source: DingtalkNodeSource, nodeId: string): string {
  return `${source}:${nodeId}`
}

export function collectDescendants(node: DingtalkDocNode): string[] {
  const ids =
    node.node_type === 'folder' ? [] : [getDingTalkSelectionKey(node.source, node.dingtalk_node_id)]
  for (const child of node.children ?? []) {
    ids.push(...collectDescendants(child))
  }
  return ids
}

export function dingTalkNodeMatchesSearch(node: DingtalkDocNode, query: string): boolean {
  const normalized = query.trim().toLowerCase()
  if (!normalized) return true
  const visit = (item: DingtalkDocNode): boolean =>
    item.name.toLowerCase().includes(normalized) || (item.children ?? []).some(visit)
  return visit(node)
}

export function filterDingTalkNodes(nodes: DingtalkDocNode[], query: string): DingtalkDocNode[] {
  const normalized = query.trim().toLowerCase()
  if (!normalized) return nodes

  const filter = (items: DingtalkDocNode[]): DingtalkDocNode[] =>
    items.reduce<DingtalkDocNode[]>((result, node) => {
      const children = filter(node.children ?? [])
      if (node.name.toLowerCase().includes(normalized) || children.length > 0) {
        result.push({ ...node, children })
      }
      return result
    }, [])

  return filter(nodes)
}

export function isNodeFullySelected(node: DingtalkDocNode, selected: Set<string>): boolean {
  const allIds = collectDescendants(node)
  return allIds.length > 0 && allIds.every(id => selected.has(id))
}

export function isNodePartiallySelected(node: DingtalkDocNode, selected: Set<string>): boolean {
  const allIds = collectDescendants(node)
  const selectedCount = allIds.filter(id => selected.has(id)).length
  return selectedCount > 0 && selectedCount < allIds.length
}

export function buildDingTalkDocContext(node: DingtalkDocNode): DingTalkDocContext {
  return {
    id: getDingTalkSelectionKey(node.source, node.dingtalk_node_id),
    name: node.name,
    type: 'dingtalk_doc',
    doc_url: node.doc_url,
    node_type: node.node_type,
    dingtalk_node_id: node.dingtalk_node_id,
    content_type: node.content_type,
    source: node.source,
  }
}

export function getDingTalkSelectedIds(selectedContexts: ContextItem[]): Set<string> {
  return new Set(
    selectedContexts
      .filter((context): context is DingTalkDocContext => context.type === 'dingtalk_doc')
      .map(context => getDingTalkSelectionKey(context.source, context.dingtalk_node_id))
  )
}
