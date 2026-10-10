// Chat turn transport layer for the ZY-Agents backend.
//
// The flow has two steps:
//   a. POST /conversations/{id}/turns  body {request_id, content:[{type:'text',
//      data:{text}}]} — waits for the model reply to finish; 409 means busy.
//   b. GET  /conversations/{id}/stream — SSE (`id: {cursor}\ndata: {json}\n\n`),
//      read in parallel while the turn POST is still running. Every SSE data
//      frame is translated into TimelineEventV3 (payload item_kind/item_id/
//      text/stream_seq hoisted to the top level) and pushed into the execution
//      store until a terminal event (run.completed / turn.error) shows up.

import type { TimelineEventV3, TimelineEventType } from '../modules/timeline-v3/domain/protocol'
import { notifyAuthExpiredFromResponse } from '../api/authExpiry'

const API_BASE = '/api/v1'

/** Server contract for a 409 on turn POST: another run currently owns the conversation. */
export const TURN_ALREADY_RUNNING = 'session_already_running'

/**
 * A non-OK chat response. `code` carries the server contract code; callers
 * tell server contracts apart by status + code, never by the display message.
 */
export class TurnStreamHttpError extends Error {
  status: number
  code: string | null

  constructor(status: number, code: string | null, message: string) {
    super(message)
    this.name = 'TurnStreamHttpError'
    this.status = status
    this.code = code
  }
}

export interface TurnStreamRequest {
  messages: any[]
  modelId?: string
  output_spec?: Record<string, any>
  knowledgeQaEnabled?: boolean
  knowledgeBaseIds?: string[]
  timezone?: string
}

export interface TurnStreamHandle {
  /** Conversation id on the backend (created on demand when the pane had none) */
  sessionId: string | null
  /** Request id for this turn (doubles as the event-history recovery key) */
  messageId: string | null
  /** Settles after the turn POST has gone out (response headers known). */
  ready: Promise<void>
  /** Settles once the stream has finished (or aborted gracefully) */
  done: Promise<void>
  /** Cancels the local fetches (backend is NOT notified; use cancelTurn() for that) */
  abort: () => void
}

/** SSE event types that terminate and close the stream. */
const TERMINAL_EVENT_TYPES = new Set<string>(['run.completed', 'run.failed', 'run.cancelled'])

/** In-flight request ids keyed by conversation (serve as cancel + recovery keys). */
const activeRequestByConversation = new Map<string, string>()
const conversationByRequest = new Map<string, string>()

function newRequestId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return `req_${Date.now()}_${Math.random().toString(36).slice(2)}`
}

function authHeaders(authToken?: string | null): Record<string, string> {
  return authToken ? { Authorization: `Bearer ${authToken}` } : {}
}

async function errorMessageOf(resp: Response): Promise<string> {
  try {
    const payload = await resp.json()
    return String(
      payload?.error?.message
      || payload?.detail?.message
      || (typeof payload?.detail === 'string' ? payload.detail : '')
      || `The request did not succeed: ${resp.status}`,
    )
  } catch {
    return `The request did not succeed: ${resp.status}`
  }
}

/** The latest user input — the turn content the backend will execute. */
function lastUserText(messages: any[]): string {
  for (let i = messages.length - 1; i >= 0; i--) {
    const message = messages[i]
    if (message?.role === 'user' && typeof message?.content === 'string' && message.content.trim()) {
      return message.content.trim()
    }
  }
  return ''
}

/**
 * Translates one SSE data frame ({event_id, cursor, type, occurred_at, payload})
 * into TimelineEventV3 events. payload item_kind/item_id/text/stream_seq get
 * hoisted to the top level; item revisions are tracked individually so the
 * monotonic ordering that ExecutionStoreV3's dedup expects still holds even
 * when the backend restarts revision at 0 per item. turn.error expands into
 * an error item plus a terminal run.failed so the runtime sees the turn as
 * finished.
 */
export function createSseEventMapper() {
  const itemRevisions = new Map<string, number>()
  let fallbackSeq = 0

  return function mapSseEvent(data: any): TimelineEventV3[] {
    if (!data || typeof data !== 'object') return []
    const type = String(data.type || '')
    const payload = data.payload && typeof data.payload === 'object' ? data.payload : {}
    // naive ISO（无 Z / 时区后缀）语义为 UTC：Date.parse 按本地时区解析会偏移，
    // run 时长恰好虚增一个 UTC 偏移量（东八区即 8 小时）——这里统一校正。
    const occurredAtRaw = String(data.occurred_at || '')
    let parsedTs = Date.parse(occurredAtRaw)
    if (Number.isFinite(parsedTs) && occurredAtRaw && !/(?:Z|[+-]\d{2}:?\d{2})$/i.test(occurredAtRaw)) {
      parsedTs -= new Date(parsedTs).getTimezoneOffset() * 60_000
    }
    const ts = Number.isFinite(parsedTs) ? parsedTs : Date.now()
    const eventId = String(data.event_id || `evt_${data.cursor ?? ++fallbackSeq}`)
    const runPayload = { run_id: payload.run_id, request_id: payload.request_id }

    if (type === 'run.started') {
      return [{ v: 3, event_id: eventId, id: eventId, ts, type: 'run.started', revision: 0, payload: runPayload }]
    }
    if (type === 'run.completed') {
      return [{
        v: 3, event_id: eventId, id: eventId, ts, type: 'run.completed', revision: 0,
        payload: { ...runPayload, finish_reason: payload.finish_reason },
      }]
    }
    if (type === 'turn.error') {
      const message = String(payload.message || 'turn error')
      return [
        {
          v: 3, event_id: `${eventId}#item`, id: `${eventId}#item`, ts, type: 'item.failed',
          item_kind: 'error', item_id: 'turn_error', revision: 1, payload: { message },
        },
        {
          v: 3, event_id: `${eventId}#run`, id: `${eventId}#run`, ts, type: 'run.failed',
          revision: 0, payload: { message },
        },
      ]
    }
    if (type === 'item.started' || type === 'item.delta' || type === 'item.completed') {
      const itemKind = String(payload.item_kind || '')
      const itemId = String(payload.item_id || '')
      if (!itemId || !itemKind) return []
      const tracked = itemRevisions.get(itemId) ?? -1
      let revision = typeof payload.revision === 'number' ? payload.revision : 0
      if (type === 'item.delta' && typeof payload.stream_seq === 'number') {
        revision = Math.max(revision, payload.stream_seq + 1)
      }
      revision = Math.max(revision, tracked + 1)
      itemRevisions.set(itemId, revision)
      if (itemRevisions.size > 500) {
        // Bound the map over long-running sessions; stale ids are never looked
        // up again because terminal turns are not replayed live.
        const drop = itemRevisions.keys().next().value
        if (drop !== undefined) itemRevisions.delete(drop)
      }
      // 结构化字段透传（name/args/ok/result）：工具时间线需要工具名、入参摘要
      // 与成败状态，仅靠 text 摘要无法渲染透明的执行过程。
      const itemPayload: Record<string, any> = { text: String(payload.text ?? '') }
      if (payload.name) itemPayload.name = String(payload.name)
      if (payload.args !== undefined && payload.args !== null) itemPayload.args = payload.args
      if (payload.ok !== undefined) itemPayload.ok = payload.ok === true || payload.ok === 'true'
      if (payload.result !== undefined && payload.result !== null) itemPayload.result = String(payload.result)
      const event: TimelineEventV3 = {
        v: 3,
        event_id: eventId,
        id: eventId,
        ts,
        type: type as TimelineEventType,
        item_kind: itemKind as TimelineEventV3['item_kind'],
        item_id: itemId,
        revision,
        payload: itemPayload,
      }
      if (type === 'item.delta' && typeof payload.stream_seq === 'number') {
        event.stream_seq = payload.stream_seq
      }
      return [event]
    }
    return []
  }
}

/** Module-level shared mapper: live streams and history-recovery replays share
 *  the per-item revision tracking, keeping mixed replays monotonic. */
const sharedMapper = createSseEventMapper()

/** Turns one SSE frame into mapped V3 events (data: lines only). */
function mapSseFrame(frame: string): TimelineEventV3[] {
  for (const rawLine of frame.split('\n')) {
    const line = rawLine.trim()
    if (!line.startsWith('data:')) continue
    const json = line.slice(5).trim()
    if (!json || json === '[DONE]') return []
    try {
      return sharedMapper(JSON.parse(json))
    } catch {
      console.warn('[chat-stream] unparseable sse frame', json)
      return []
    }
  }
  return []
}

/**
 * Opens the SSE stream and pumps translated V3 events into onEvent until a
 * terminal event shows up (the reader is then cancelled — the backend keeps
 * the connection open polling for roughly 60s after the final event) or the
 * stream ends.
 */
async function openSseStream(
  url: string,
  headers: Record<string, string>,
  signal: AbortSignal,
  onEvent: (ev: TimelineEventV3) => void,
): Promise<void> {
  const resp = await fetch(url, { method: 'GET', headers, signal })
  if (!resp.ok || !resp.body) return // the turn result will surface the failure
  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let settled = false
  try {
    while (!settled) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      let separator = buffer.indexOf('\n\n')
      while (separator >= 0) {
        const frame = buffer.slice(0, separator)
        buffer = buffer.slice(separator + 2)
        for (const event of mapSseFrame(frame)) {
          if (TERMINAL_EVENT_TYPES.has(event.type)) settled = true
          onEvent(event)
        }
        separator = buffer.indexOf('\n\n')
      }
    }
  } finally {
    if (settled) reader.cancel().catch(() => { /* closing already */ })
  }
}

export function startTurnStream(
  body: TurnStreamRequest,
  onEvent: (ev: TimelineEventV3) => void,
  opts: {
    authToken?: string | null
    onSessionId?: (sid: string) => void
    onMessageId?: (mid: string) => void
  } = {},
): TurnStreamHandle {
  const ctrl = new AbortController()
  let resolveReady: () => void = () => { /* settled later */ }
  const handle: TurnStreamHandle = {
    sessionId: null,
    messageId: null,
    ready: new Promise<void>((resolve) => { resolveReady = resolve }),
    done: Promise.resolve(),
    abort: () => ctrl.abort(),
  }

  const runTurn = async (): Promise<void> => {
    const headers: Record<string, string> = { 'Content-Type': 'application/json', ...authHeaders(opts.authToken) }

    // 1. Settle the conversation: reuse the pane's session, or create one first.
    let conversationId = String(body.output_spec?.task_id || '')
    if (!conversationId) {
      const title = lastUserText(body.messages).slice(0, 50) || 'New conversation'
      const createResp = await fetch(`${API_BASE}/conversations`, {
        method: 'POST',
        headers,
        body: JSON.stringify({ title }),
        signal: ctrl.signal,
      })
      notifyAuthExpiredFromResponse(createResp, Boolean(opts.authToken))
      if (!createResp.ok) {
        throw new TurnStreamHttpError(createResp.status, null, await errorMessageOf(createResp))
      }
      const created = await createResp.json()
      conversationId = String(created?.id || '')
      if (!conversationId) {
        throw new TurnStreamHttpError(500, null, 'could not create conversation')
      }
      handle.sessionId = conversationId
      opts.onSessionId?.(conversationId)
    }

    // 2. Start the SSE stream and the turn POST in parallel: the turn POST
    //    waits for the model reply to finish while the stream delivers the
    //    run/item events as they are persisted.
    const requestId = newRequestId()
    handle.messageId = requestId
    opts.onMessageId?.(requestId)
    activeRequestByConversation.set(conversationId, requestId)
    conversationByRequest.set(requestId, conversationId)

    try {
      const streamPromise = openSseStream(
        `${API_BASE}/conversations/${encodeURIComponent(conversationId)}/stream`,
        headers,
        ctrl.signal,
        onEvent,
      )

      const turnResp = await fetch(`${API_BASE}/conversations/${encodeURIComponent(conversationId)}/turns`, {
        method: 'POST',
        headers,
        body: JSON.stringify({
          request_id: requestId,
          content: [{ type: 'text', data: { text: lastUserText(body.messages) } }],
        }),
        signal: ctrl.signal,
      })
      notifyAuthExpiredFromResponse(turnResp, Boolean(opts.authToken))
      resolveReady()
      if (turnResp.status === 409) {
        throw new TurnStreamHttpError(409, TURN_ALREADY_RUNNING, 'conversation is busy')
      }
      if (!turnResp.ok) {
        throw new TurnStreamHttpError(turnResp.status, null, await errorMessageOf(turnResp))
      }

      // 3. Drain the stream until the terminal event (or the idle-timeout close).
      await streamPromise
    } finally {
      // Cancellation is no longer accepted once the turn settles; remove the
      // in-flight keys so stale ids cannot leak between turns.
      activeRequestByConversation.delete(conversationId)
      conversationByRequest.delete(requestId)
    }
  }
  // Setup failed before the turn POST got through: settle readiness so the
  // stop flow never waits forever on headers that will not arrive.
  handle.done = runTurn().catch((error) => {
    resolveReady()
    throw error
  })

  return handle
}

/** Issues POST /conversations/{id}/turns/{request_id}/cancel {cause} — true when acked. */
export async function cancelTurn(sessionId: string, authToken?: string | null): Promise<boolean> {
  if (!sessionId) return false
  const requestId = activeRequestByConversation.get(sessionId)
  if (!requestId) return false
  try {
    const resp = await fetch(
      `${API_BASE}/conversations/${encodeURIComponent(sessionId)}/turns/${encodeURIComponent(requestId)}/cancel`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...authHeaders(authToken),
        },
        body: JSON.stringify({ cause: 'user_requested' }),
        keepalive: true,
      },
    )
    return resp.ok
  } catch {
    return false
  }
}

/**
 * Test seam: register or clear the in-flight request id for a conversation
 * (its cancel key). Production code fills this in via startTurnStream; tests
 * use it to drive the cancel acknowledgement path directly.
 */
export function setActiveRequestForTest(conversationId: string, requestId: string | null): void {
  if (requestId === null) {
    activeRequestByConversation.delete(conversationId)
    return
  }
  activeRequestByConversation.set(conversationId, requestId)
}

/**
 * Event history used for reconnect recovery. Maps a per-turn message id (the
 * turn request_id) back to its conversation and replays
 * GET /conversations/{id}/events?after_cursor=. With no live mapping (say,
 * after a page reload) it reports a settled page so the recovery loop stops
 * instead of polling indefinitely.
 */
export async function fetchChatMessageEvents(
  messageId: string,
  after: number,
  opts: { authToken?: string | null } = {},
): Promise<{
  events: TimelineEventV3[]
  next_index: number
  next_cursor: number
  status: string
  live: boolean
}> {
  const cursor = Math.max(0, after || 0)
  const conversationId = conversationByRequest.get(messageId)
  if (!conversationId) {
    return { events: [], next_index: cursor, next_cursor: cursor, status: 'completed', live: false }
  }
  const headers: Record<string, string> = authHeaders(opts.authToken)
  const query = new URLSearchParams({ after_cursor: String(cursor) })
  const resp = await fetch(
    `${API_BASE}/conversations/${encodeURIComponent(conversationId)}/events?${query.toString()}`,
    { method: 'GET', headers },
  )
  notifyAuthExpiredFromResponse(resp, Boolean(opts.authToken))
  if (!resp.ok) throw new Error(`Recovery request failed: ${resp.status}`)
  const payload = await resp.json()
  const items = Array.isArray(payload)
    ? payload
    : Array.isArray(payload?.items)
      ? payload.items
      : Array.isArray(payload?.events)
        ? payload.events
        : []
  const events = items.flatMap((item: any) => sharedMapper(item))
  const terminalSeen = events.some((event: TimelineEventV3) => TERMINAL_EVENT_TYPES.has(event.type))
  const nextCursor = events.reduce(
    (max: number, event: TimelineEventV3) => Math.max(max, Number(event.stream_seq || 0)),
    cursor,
  )
  return {
    events,
    next_index: nextCursor,
    next_cursor: nextCursor,
    status: terminalSeen ? 'completed' : 'live',
    live: !terminalSeen,
  }
}

/** ZY-Agents 阶段 1 无工具审批端点：保持签名的 no-op。 */
export async function resolvePermission(
  _sessionId: string,
  _requestId: string,
  _decision: 'allow' | 'deny' | 'always_allow',
  _extras: { reason?: string; updated_args?: Record<string, any> } = {},
): Promise<void> {
  // no-op
}
