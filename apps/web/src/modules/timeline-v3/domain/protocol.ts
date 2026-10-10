export type TimelineEventType =
  | 'run.started' | 'run.completed' | 'run.failed' | 'run.cancelled' | 'run.blocked'
  | 'item.started' | 'item.updated' | 'item.delta' | 'item.completed' | 'item.failed'

export type V3ItemKind =
  | 'commentary' | 'activity' | 'final_answer' | 'tool' | 'subagent' | 'approval'
  | 'browser_handoff' | 'browser_preview' | 'artifact' | 'evidence' | 'error'

export interface TimelineEventV3 {
  v: 3
  event_id: string
  id: string
  ts: number
  type: TimelineEventType
  item_kind?: V3ItemKind
  item_id?: string
  parent_item_id?: string
  revision: number
  stream_seq?: number
  stream_seq_end?: number
  payload: Record<string, any>
}

export function isTimelineEventV3(value: any): value is TimelineEventV3 {
  return Number(value?.v) === 3 && typeof value?.type === 'string' && typeof value?.event_id === 'string'
}
