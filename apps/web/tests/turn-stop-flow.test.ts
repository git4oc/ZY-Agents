import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import test from 'node:test'
import { stopTurnGeneration, type StoppableTurnPane } from '../src/hooks/turnStopFlow'
import { cancelTurn, setActiveRequestForTest, startTurnStream } from '../src/hooks/useTurnStream'

const root = process.cwd()
const runtime = readFileSync(resolve(root, 'src/hooks/useTurnRuntimeStore.ts'), 'utf8')
const cancellation = readFileSync(resolve(root, 'src/hooks/turnStopFlow.ts'), 'utf8')
const app = readFileSync(resolve(root, 'src/App.vue'), 'utf8')
const actionButton = readFileSync(resolve(root, 'src/ui/chat/ComposerActionButton.vue'), 'utf8')
const executionView = readFileSync(resolve(root, 'src/modules/timeline-v3/ui/TimelineViewV3.vue'), 'utf8')
const localeMessages = readFileSync(resolve(root, 'src/locales/messages.ts'), 'utf8')

assert.ok(runtime.includes('stopTurnGeneration(pane'), 'runtime store must delegate cancellation')
assert.ok(cancellation.includes('pane.stopping = true'), 'stop must expose a stopping phase')
assert.ok(cancellation.includes('const cancelled = await cancelTurn'), 'stop must await server acknowledgement')
assert.ok(!cancellation.includes('1200'), 'stop must not optimistically time out after 1.2 seconds')
assert.ok(!cancellation.includes("type: 'run.cancelled'"), 'frontend must not forge a terminal cancellation event')
assert.ok(cancellation.includes('await handle.ready'), 'stop must wait for authoritative response headers')
assert.ok(!cancellation.includes('attempt < 50'), 'stop must not poll for a server identity')
assert.ok(
  cancellation.indexOf('const cancelled = await cancelTurn') < cancellation.lastIndexOf('releaseStoppedPane(pane, setRunning)'),
  'the composer may unlock only after cancellation is acknowledged',
)
assert.ok(app.includes(':stopping="pane.stopping || codeRuntime.stateFor(pane.key).stopping"'), 'chat and Code stopping state must reach the visible composer')
assert.match(actionButton, /v-if="running" class="composer-action__ring"/, 'a running task must render the outer progress ring')
assert.match(actionButton, /width: 40px;[\s\S]*width: 32px;/, 'the progress ring must remain visible outside the stop button')
assert.match(executionView, /class="timeline-v3-thinking">\{\{ t\('execution\.v3\.thinking'\) \}\}<\/span>/, 'a live task without activity rows must show an explicit thinking label')
assert.match(executionView, /animation:thinking-sheen 1\.7s linear infinite/, 'the full thinking label must use the running sheen')
assert.doesNotMatch(executionView, /timeline-v3-placeholder-dot/, 'the thinking state must not render a separate loading icon')
assert.match(localeMessages, /'execution\.v3\.thinking': \{ zh: '思考中\.\.\.', en: 'Thinking\.\.\.' \}/, 'thinking copy must keep equivalent Chinese and English ellipses')

/** 两步传输桩：创建会话 → JSON {id}，turn POST →
 *  空响应体，SSE 流 → 空响应体。按方法与 URL 后缀分发。 */
function mockTwoStepFetch(turnStatus = 200) {
  return (async (input: any, init?: RequestInit) => {
    const url = String(typeof input === 'string' ? input : input.url)
    const method = (init?.method ?? 'GET').toUpperCase()
    if (method === 'POST' && url.endsWith('/conversations')) {
      return new Response(JSON.stringify({ id: 'conversation-a' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    }
    if (method === 'POST' && url.endsWith('/turns')) {
      return new Response('', { status: turnStatus })
    }
    if (method === 'GET' && url.endsWith('/stream')) {
      return new Response('', { status: 200 })
    }
    throw new Error('unexpected fetch: ' + method + ' ' + url)
  }) as typeof fetch
}

test('stream readiness waits for authoritative response headers', async () => {
  const originalFetch = globalThis.fetch
  globalThis.fetch = mockTwoStepFetch()
  try {
    const handle = startTurnStream({ messages: [] }, () => undefined)
    let ready = false
    handle.ready.then(() => { ready = true })
    await Promise.resolve()
    assert.equal(ready, false)

    // 就绪判定等的是 turn POST 结束，而非创建会话请求
    await handle.ready
    assert.equal(handle.sessionId, 'conversation-a')
    assert.ok(typeof handle.messageId === 'string' && handle.messageId.length > 0)
    await handle.done
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('stream readiness also settles when request setup fails', async () => {
  const originalFetch = globalThis.fetch
  globalThis.fetch = (async () => { throw new Error('offline') }) as typeof fetch
  try {
    const handle = startTurnStream({ messages: [] }, () => undefined)
    const done = handle.done.catch(() => undefined)
    await handle.ready
    await done
    assert.equal(handle.sessionId, null)
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('cancellation requires the API business acknowledgement', async () => {
  const originalFetch = globalThis.fetch
  let cancelStatus = 200
  // 在取消发生期间让 turn POST 保持未决——真实流程是
  // 取消一个仍在生成的 turn，而未决请求的键会在 turn
  // 落定的瞬间被丢弃。
  let releaseTurn!: (response: Response) => void
  const turnGate = new Promise<Response>((resolve) => { releaseTurn = resolve })
  globalThis.fetch = (async (input: any, init?: RequestInit) => {
    const url = String(typeof input === 'string' ? input : input.url)
    const method = (init?.method ?? 'GET').toUpperCase()
    if (method === 'POST' && url.endsWith('/cancel')) {
      return new Response(
        JSON.stringify({ code: cancelStatus === 200 ? 0 : 404, message: 'ok' }),
        { status: cancelStatus, headers: { 'Content-Type': 'application/json' } },
      )
    }
    if (method === 'POST' && url.endsWith('/conversations')) {
      return new Response(JSON.stringify({ id: 'conversation-cancel' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    }
    if (method === 'POST' && url.endsWith('/turns')) {
      return turnGate
    }
    return new Response('', { status: 200 })
  }) as typeof fetch
  try {
    // 没有未决请求时无从取消——不发起 API 调用
    assert.equal(await cancelTurn('conversation-cancel'), false)

    let assignedMessageId: string | null = null
    const handle = startTurnStream({ messages: [] }, () => undefined, {
      onMessageId: (mid) => { assignedMessageId = mid },
    })
    for (let i = 0; i < 50 && !assignedMessageId; i++) await Promise.resolve()
    assert.ok(assignedMessageId)
    assert.ok(handle.messageId)

    cancelStatus = 404
    assert.equal(await cancelTurn('conversation-cancel'), false)

    cancelStatus = 200
    assert.equal(await cancelTurn('conversation-cancel'), true)

    releaseTurn(new Response('', { status: 200 }))
    await handle.done
  } finally {
    globalThis.fetch = originalFetch
  }
})

function makePane(sessionId = 'conversation-a') {
  let aborted = false
  const handle = {
    sessionId,
    messageId: 'message-a',
    ready: Promise.resolve(),
    done: Promise.resolve(),
    abort: () => { aborted = true },
  }
  const pane: StoppableTurnPane = {
    sessionId,
    messages: [{ _id: 'assistant-a', role: 'assistant', _backendSid: sessionId }],
    stopping: false,
    operationId: 1,
    activeStream: handle,
    abortController: new AbortController(),
    authResumeController: null,
    activeAssistantMessageId: 'assistant-a',
    activeAuthToken: 'token-a',
  }
  return { pane, handle, wasAborted: () => aborted }
}

test('acknowledged cancellation unlocks the pane and aborts its local stream', async () => {
  const originalFetch = globalThis.fetch
  globalThis.fetch = (async () => new Response(
    JSON.stringify({ code: 0, message: 'ok' }),
    { status: 200, headers: { 'Content-Type': 'application/json' } },
  )) as typeof fetch
  setActiveRequestForTest('conversation-ack', 'request-ack')
  try {
    const { pane, wasAborted } = makePane('conversation-ack')
    const runningStates: boolean[] = []
    assert.equal(await stopTurnGeneration(pane, (running) => runningStates.push(running)), true)
    assert.equal(pane.activeStream, null)
    assert.equal(pane.stopping, false)
    assert.equal(wasAborted(), true)
    assert.deepEqual(runningStates, [false])
  } finally {
    setActiveRequestForTest('conversation-ack', null)
    globalThis.fetch = originalFetch
  }
})

test('rejected cancellation preserves the active pane lock for a safe retry', async () => {
  const originalFetch = globalThis.fetch
  globalThis.fetch = (async () => new Response(
    JSON.stringify({ code: 503, message: 'turn_still_stopping' }),
    { status: 503, headers: { 'Content-Type': 'application/json' } },
  )) as typeof fetch
  setActiveRequestForTest('conversation-reject', 'request-reject')
  try {
    const { pane, handle, wasAborted } = makePane('conversation-reject')
    const runningStates: boolean[] = []
    assert.equal(await stopTurnGeneration(pane, (running) => runningStates.push(running)), false)
    assert.equal(pane.activeStream, handle)
    assert.equal(pane.stopping, false)
    assert.equal(wasAborted(), false)
    assert.deepEqual(runningStates, [])
  } finally {
    setActiveRequestForTest('conversation-reject', null)
    globalThis.fetch = originalFetch
  }
})

test('stopping preflight work invalidates the pending operation without calling the API', async () => {
  const originalFetch = globalThis.fetch
  let fetched = false
  globalThis.fetch = (async () => {
    fetched = true
    throw new Error('unexpected fetch')
  }) as typeof fetch
  try {
    const { pane } = makePane()
    pane.activeStream = null
    const runningStates: boolean[] = []
    assert.equal(await stopTurnGeneration(pane, (running) => runningStates.push(running)), true)
    assert.equal(pane.operationId, 2)
    assert.equal(pane.stopping, false)
    assert.equal(fetched, false)
    assert.deepEqual(runningStates, [false])
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('a failed stream without a server identity releases local running state', async () => {
  const { pane, wasAborted } = makePane()
  pane.sessionId = null
  pane.messages = [{ _id: 'assistant-a', role: 'assistant' }]
  pane.activeStream!.sessionId = null
  const runningStates: boolean[] = []
  assert.equal(await stopTurnGeneration(pane, (running) => runningStates.push(running)), true)
  assert.equal(pane.operationId, 2)
  assert.equal(pane.activeStream, null)
  assert.equal(pane.stopping, false)
  assert.equal(wasAborted(), true)
  assert.deepEqual(runningStates, [false])
})
