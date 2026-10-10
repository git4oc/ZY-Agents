import { t } from '../../hooks/i18n'

const PROCESSING_STATUSES = new Set([
  'uploading',
  'uploaded',
  'pending_parse',
  'parsed',
  'processing',
])

export function isKnowledgeProcessingStatus(status: string): boolean {
  return PROCESSING_STATUSES.has(status)
}

export function knowledgeStatusText(status: string, revoked = false): string {
  if (revoked) return t('knowledge.status_revoked')
  // 后端完成态是 ready（#9 上传异步化）；indexed 为历史兼容取值
  if (status === 'indexed' || status === 'ready') return t('knowledge.status_indexed')
  if (status === 'failed') return t('knowledge.status_failed')
  if (status === 'uploading') return t('knowledge.status_uploading')
  if (status === 'parsed') return t('knowledge.status_indexing')
  return t('knowledge.status_learning')
}
