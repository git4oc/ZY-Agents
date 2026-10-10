import { notifyAuthExpiredFromResponse } from '../../api/authExpiry'
import type { TurnStreamHandle } from '../useTurnStream'
import type { TimelineEventV3 } from '../../modules/timeline-v3/domain/protocol'
import { createStreamReadiness } from './streamSettle'

export function startTaskResumeStream(input: {
  runId: string
  suspensionId: string
  nodeId: string
  messages: any[]
  modelId?: string
  signal?: Record<string, any>
}, onEvent: (event: TimelineEventV3) => void, options: {
  authToken: string
  onAccepted?: () => void
  onMessageId?: (messageId: string) => void
}): TurnStreamHandle {
  const controller = new AbortController()
  const readiness = createStreamReadiness()
  const handle: TurnStreamHandle = {
    sessionId: null,
    messageId: null,
    ready: readiness.ready,
    done: Promise.resolve(),
    abort: () => controller.abort(),
  }
  handle.done = (async () => {
    try {
      const response = await fetch(`/api/tasks/${encodeURIComponent(input.runId)}/resume`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${options.authToken}`,
        },
        body: JSON.stringify({
          suspension_id: input.suspensionId,
          node_id: input.nodeId,
          messages: input.messages,
          model_id: input.modelId || '',
          signal: input.signal || {},
        }),
        signal: controller.signal,
      })
      notifyAuthExpiredFromResponse(response, true)
      if (!response.ok) throw new Error(await responseError(response))
      options.onAccepted?.()
      handle.sessionId = response.headers.get('X-Session-Id')
      handle.messageId = response.headers.get('X-Message-Id')
      if (handle.messageId) options.onMessageId?.(handle.messageId)
      readiness.settle()
      if (!response.body) throw new Error('Response body is missing')
      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() || ''
        for (const line of lines) emitLine(line, onEvent)
      }
      emitLine(buffer, onEvent)
    } finally {
      readiness.settle()
    }
  })()
  return handle
}

function emitLine(line: string, onEvent: (event: TimelineEventV3) => void): void {
  const value = line.trim()
  if (!value) return
  try { onEvent(JSON.parse(value) as TimelineEventV3) } catch { /* skip malformed transport lines */ }
}

async function responseError(response: Response): Promise<string> {
  try {
    const payload = await response.json()
    return String(payload?.detail?.message || payload?.detail || payload?.message || `The request did not succeed: ${response.status}`)
  } catch {
    return `The request did not succeed: ${response.status}`
  }
}
