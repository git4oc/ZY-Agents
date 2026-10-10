import type { ExecutionItemV3, V3ItemStatus } from './model'

export type ActivityOutcome = 'inconclusive' | 'needs_repair' | 'passed' | ''

export function activityOutcome(payload: Record<string, any> | undefined): ActivityOutcome {
  const value = String(payload?.detail?.outcome || payload?.outcome || '')
  return ['inconclusive', 'needs_repair', 'passed'].includes(value)
    ? value as ActivityOutcome
    : ''
}

export function activityStateMessageKey(
  status: V3ItemStatus,
  outcome: ActivityOutcome,
  payload?: Record<string, any>,
): string {
  if (outcome === 'inconclusive') {
    const errorType = String(payload?.detail?.error_type || payload?.error_type || '').toLowerCase()
    return errorType.includes('timeout')
      ? 'execution.v3.quality_timeout'
      : 'execution.v3.quality_inconclusive'
  }
  if (outcome === 'needs_repair') return 'execution.v3.quality_needs_repair'
  if (status === 'failed') return 'ui.failed'
  return ''
}

export function runningContainerIds(items: ExecutionItemV3[]): Set<string> {
  const byId = new Map(items.map((item) => [item.id, item]))
  const containers = new Set<string>()

  // 已在展示子项的运行中条目是分组节点，而非当前活动叶子。
  // 在下一个子项启动前的短暂间隙内，已完成的子项仍维持该归属关系。
  for (const item of items) {
    const visited = new Set<string>()
    let parentId = item.parentId
    while (parentId && !visited.has(parentId)) {
      visited.add(parentId)
      const parent = byId.get(parentId)
      if (!parent) break
      if (parent.status === 'running') containers.add(parent.id)
      parentId = parent.parentId
    }
  }
  return containers
}

export function hasActiveRunningLeaf(
  items: ExecutionItemV3[],
  containerIds = runningContainerIds(items),
): boolean {
  return items.some((item) => item.status === 'running' && !containerIds.has(item.id))
}

function hasVisibleProgressContent(item: ExecutionItemV3): boolean {
  const payload = item.payload || {}
  if (item.kind === 'tool') return true
  if (['commentary', 'final_answer'].includes(item.kind)) return Boolean(String(payload.text || '').trim())
  if (item.kind === 'activity') {
    return String(payload.category || '') === 'skill' || Boolean(String(payload.label || '').trim())
  }
  if (item.kind === 'subagent') return Boolean(String(payload.goal || payload.summary || '').trim())
  return ['approval', 'browser_handoff', 'error'].includes(item.kind)
}

/** 仅当活动叶子确实能在时间线中呈现可见进度时为真。 */
export function hasVisibleActiveRunningLeaf(
  items: ExecutionItemV3[],
  containerIds = runningContainerIds(items),
): boolean {
  return items.some((item) => (
    item.status === 'running'
    && !containerIds.has(item.id)
    && hasVisibleProgressContent(item)
  ))
}
