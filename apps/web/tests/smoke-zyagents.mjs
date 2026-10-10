// ZY-Agents 前端适配冒烟：登录 → /auth/me → 建会话 → 发 turn → 读 SSE。
// 默认走 vite 代理（同源路径，模拟浏览器真实请求）；SMOKE_BASE 可覆盖。
const BASE = process.env.SMOKE_BASE || 'http://127.0.0.1:4173/api/v1'

async function main() {
  // 1. login
  const loginResp = await fetch(`${BASE}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: process.env.SMOKE_USER || 'admin', password: process.env.SMOKE_PASS || 'admin' }),
  })
  console.log('[smoke] login status', loginResp.status)
  if (!loginResp.ok) {
    console.log('[smoke] login body', await loginResp.text())
    process.exit(1)
  }
  const { access_token: token } = await loginResp.json()
  console.log('[smoke] token acquired:', String(token).slice(0, 20) + '...')
  const auth = { Authorization: `Bearer ${token}` }

  // 2. /auth/me
  const meResp = await fetch(`${BASE}/auth/me`, { headers: auth })
  const me = await meResp.json()
  console.log('[smoke] me:', JSON.stringify(me).slice(0, 200))

  // 3. create conversation
  const convResp = await fetch(`${BASE}/conversations`, {
    method: 'POST',
    headers: { ...auth, 'Content-Type': 'application/json' },
    body: JSON.stringify({ title: 'smoke-test' }),
  })
  console.log('[smoke] create conversation status', convResp.status)
  const conv = await convResp.json()
  console.log('[smoke] conversation id:', conv.id)
  if (!conv.id) process.exit(1)

  // 4. open SSE first, then post the turn (mirrors the frontend flow)
  const events = []
  const ctrl = new AbortController()
  const streamPromise = (async () => {
    const streamResp = await fetch(`${BASE}/conversations/${conv.id}/stream`, { headers: auth, signal: ctrl.signal })
    console.log('[smoke] sse status', streamResp.status)
    if (!streamResp.ok || !streamResp.body) return
    const reader = streamResp.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    let settled = false
    while (!settled) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      let sep = buffer.indexOf('\n\n')
      while (sep >= 0) {
        const frame = buffer.slice(0, sep)
        buffer = buffer.slice(sep + 2)
        for (const line of frame.split('\n')) {
          const t = line.trim()
          if (!t.startsWith('data:')) continue
          try {
            const data = JSON.parse(t.slice(5).trim())
            events.push(data)
            if (data.type === 'run.completed' || data.type === 'turn.error') settled = true
          } catch { /* ignore */ }
        }
        sep = buffer.indexOf('\n\n')
      }
    }
    try { await reader.cancel() } catch { /* ignore */ }
  })()

  const requestId = 'req-' + Date.now() + '-smoke'
  const turnResp = await fetch(`${BASE}/conversations/${conv.id}/turns`, {
    method: 'POST',
    headers: { ...auth, 'Content-Type': 'application/json' },
    body: JSON.stringify({
      request_id: requestId,
      content: [{ type: 'text', data: { text: '用一句话回答：1+1等于几？' } }],
    }),
  })
  console.log('[smoke] turn status', turnResp.status)
  if (!turnResp.ok) {
    console.log('[smoke] turn body', await turnResp.text())
    ctrl.abort()
    process.exit(1)
  }
  const turnResult = await turnResp.json()
  console.log('[smoke] turn result:', JSON.stringify(turnResult).slice(0, 200))

  await Promise.race([streamPromise, new Promise((r) => setTimeout(r, 60000))])
  try { ctrl.abort() } catch { /* ignore */ }

  // 5. messages
  const msgResp = await fetch(`${BASE}/conversations/${conv.id}/messages`, { headers: auth })
  const msgPayload = await msgResp.json()
  const items = Array.isArray(msgPayload?.items) ? msgPayload.items : []
  console.log('[smoke] messages count:', items.length)
  for (const m of items.slice(0, 4)) {
    const text = Array.isArray(m.content)
      ? m.content.map((p) => (p?.type === 'text' ? p?.data?.text : '')).filter(Boolean).join('')
      : ''
    console.log(`[smoke]   ${m.role}: ${String(text).slice(0, 120)}`)
  }

  console.log('[smoke] event types:', events.map((e) => e.type).join(','))
  const deltas = events.filter((e) => e.type === 'item.delta')
  const finalText = deltas.map((e) => String(e.payload?.text || '')).join('')
  console.log('[smoke] delta-joined answer:', finalText.slice(0, 200))
  const okRun = events.some((e) => e.type === 'run.completed')
  console.log(okRun ? '[smoke] PASS: run.completed received' : '[smoke] FAIL: no run.completed')
  process.exit(okRun ? 0 : 1)
}

main().catch((err) => {
  console.error('[smoke] error', err)
  process.exit(1)
})
