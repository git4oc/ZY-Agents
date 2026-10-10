export type SessionActivity = {
  id: string
  created_at?: string | null
  updated_at?: string | null
  last_message_at?: string | null
}

function timestamp(value?: string | null): number {
  if (!value) return 0
  const parsed = Date.parse(value)
  return Number.isFinite(parsed) ? parsed : 0
}

/**
 * Conversations are ordered by their most recent message. Empty or legacy
 * sessions fall back to their last-update and creation times so they can still
 * be found.
 */
export function sessionActivityTime(session: SessionActivity): number {
  return timestamp(session.last_message_at)
    || timestamp(session.updated_at)
    || timestamp(session.created_at)
}

export function compareSessionsByRecentActivity(
  left: SessionActivity,
  right: SessionActivity,
): number {
  const activityDelta = sessionActivityTime(right) - sessionActivityTime(left)
  return activityDelta || right.id.localeCompare(left.id)
}

export function sortSessionsByRecentActivity<T extends SessionActivity>(sessions: T[]): T[] {
  return [...sessions].sort(compareSessionsByRecentActivity)
}
