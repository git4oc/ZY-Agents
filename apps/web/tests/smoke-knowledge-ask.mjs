// 任务 #68 冒烟：登录 → 列知识文档 → askPersonalKnowledge 真实问答 → 断言 answer/citations。
// 仅访问本地后端 127.0.0.1:8000，无任何外网调用。
const BASE = 'http://127.0.0.1:8000/api/v1'
const USER = process.env.SMOKE_USER || "e2e-relay"
const PASS = process.env.SMOKE_PASS || 'x'

async function main() {
  // 1) 登录（裸 dict：{access_token, token_type, expires_in}）
  const loginResp = await fetch(`${BASE}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: USER, password: PASS }),
  })
  if (!loginResp.ok) throw new Error(`login failed: ${loginResp.status}`)
  const { access_token: token } = await loginResp.json()
  if (!token) throw new Error('no access_token in login response')
  console.log('PASS  login → access_token')

  // 2) 列知识文档
  const docsResp = await fetch(`${BASE}/knowledge/documents?view=mine&directoryId=all&keyword=&page=1&pageSize=12`, {
    headers: { Authorization: `Bearer ${token}` },
  })
  if (!docsResp.ok) throw new Error(`list documents failed: ${docsResp.status}`)
  const docs = await docsResp.json()
  const items = Array.isArray(docs?.items) ? docs.items : []
  console.log(`PASS  list documents → ${items.length} 篇`)
  if (!items.length) throw new Error('no documents to ask against')
  const doc = items[0]
  console.log(`      使用文档: ${doc.name} (${doc.id})`)

  // 3) 真实问答（阻塞到生成完成，给 120s）
  const askResp = await fetch(`${BASE}/knowledge/documents/${encodeURIComponent(doc.id)}/ask`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ query: '向量混合检索验证文档讲了什么？请用一句话概括。' }),
  })
  const body = await askResp.json().catch(() => null)
  if (!askResp.ok) {
    console.log('ask error body:', JSON.stringify(body).slice(0, 300))
    throw new Error(`ask failed: ${askResp.status}`)
  }

  // 4) 断言
  const answer = String(body?.answer || '')
  const citations = Array.isArray(body?.citations) ? body.citations : []
  console.log(`      answer.length = ${answer.length}`)
  console.log(`      citations = ${citations.length}，首条: ${JSON.stringify(citations[0]?.documentTitle || '')}`)
  console.log(`      answer 片段: ${answer.slice(0, 80).replace(/\n/g, ' ')}`)
  if (!answer.trim()) throw new Error('answer is empty')
  if (!citations.length) throw new Error('citations is empty')
  const first = citations[0]
  if (typeof first.documentTitle !== 'string' || typeof first.chunkId !== 'string') {
    throw new Error('citation shape mismatch (documentTitle/chunkId)')
  }
  console.log('PASS  ask → answer 非空 + citations 非空 + 形状正确')
  console.log('\n全部通过：#68 问答链路真实跑通')
}

main().catch((error) => {
  console.error('SMOKE FAIL:', error.message)
  process.exit(1)
})
