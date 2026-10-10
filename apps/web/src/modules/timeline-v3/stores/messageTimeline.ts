import type { ArtifactItem, ProofPackItem } from '../domain/delivery'
import type { TimelineEventV3 } from '../domain/protocol'
import { createExecutionStoreV3, type ExecutionStoreV3 } from './timelineStore'

type PersistedDocument = Partial<ArtifactItem> & { type?: string }

export interface ExecutionMessageV3 {
  _execV3?: ExecutionStoreV3
  execution_events?: unknown[]
  documents?: PersistedDocument[]
  evidence_bundles?: ProofPackItem[]
  proofPacks?: ProofPackItem[]
}

function completedItem(
  itemKind: 'artifact' | 'evidence',
  itemId: string,
  payload: Record<string, unknown>,
): TimelineEventV3 {
  return {
    v: 3,
    event_id: `history_seed_${itemId}`,
    id: `history_seed_${itemId}`,
    ts: Date.now(),
    type: 'item.completed',
    item_kind: itemKind,
    item_id: itemId,
    revision: 1,
    payload,
  }
}

/**
 * V3 执行状态的唯一挂载入口。
 * 会话接口返回 V3 事件；document/evidence 字段仅作为旧记录的兜底——
 * 那些记录的事件日志里没有持久化对应卡片。
 */
export function ensureMessageExecutionV3(message: ExecutionMessageV3): ExecutionStoreV3 {
  if (message._execV3) return message._execV3

  const store = createExecutionStoreV3()
  store.loadHistory(message.execution_events)

  if (!store.state.artifacts.length) {
    const documents = message.documents || []
    const hasRenderedDocument = documents.some((document) => document.type !== 'md')
    const visibleDocuments = hasRenderedDocument
      ? documents.filter((document) => document.type !== 'md')
      : documents
    visibleDocuments.forEach((document, index) => {
      const identity = String(document.id || document.object_path || document.filename || index)
      store.applyEvent(completedItem('artifact', `artifact_${identity}`, {
        ...document,
        kind: document.kind || document.type || 'document',
      }))
    })
  }

  if (!store.state.proofPacks.length) {
    const bundles = message.evidence_bundles || message.proofPacks || []
    bundles.forEach((bundle, index) => {
      const identity = String(bundle.id || index)
      store.applyEvent(completedItem('evidence', `evidence_${identity}`, { ...bundle }))
    })
  }

  message._execV3 = store
  return store
}
