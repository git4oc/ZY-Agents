import type { SessionSummary } from '../api/sessions'

export type ForeignRun = {
  messageId: string
  initiatorUserId: string
  status: string
}

export function foreignRunForViewer(
  activeRun: SessionSummary['active_run'],
  viewerUserId: string,
): ForeignRun | null {
  if (!activeRun || !['running', 'suspended'].includes(activeRun.status)) return null
  if (activeRun.initiator_user_id && activeRun.initiator_user_id === viewerUserId) return null
  return {
    messageId: activeRun.message_id,
    initiatorUserId: activeRun.initiator_user_id || '',
    status: activeRun.status,
  }
}
