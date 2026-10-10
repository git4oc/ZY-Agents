// ZY-Agents 我的知识库（阶段 2 任务 #2）：接真后端 `/api/knowledge/*`
// （knowledge_user.py；经 vite proxy `/api` → `/api/v1` 归一化）。
// 分享 / 授权（share/grant/revoke）接真 knowledge_shares 集合；dismiss 为前端态。

import { notifyAuthExpiredFromResponse } from './authExpiry'

export interface KnowledgeDirectory { id: string; name: string; parentId: string; count: number; children: KnowledgeDirectory[] }
export interface PersonalKnowledge {
  id: string; ownerUserId: string; directoryId: string; name: string; description: string
  tags: string[]; status: string; error: string; activeDocumentId: string
  canReshare: boolean; accessStatus: 'owner' | 'active' | 'revoked' | 'deleted'; grantedByUserId: string
  owner?: { displayName?: string; username?: string }; grantedBy?: { displayName?: string; username?: string }
  seen: boolean; sharedAt: string; createdAt: string; updatedAt: string; deleted?: boolean; deletedAt?: string; isOwner?: boolean; canShare?: boolean
  feedback?: { unreadCount: number; latestUnreadAt: string }
  share?: { shared: boolean; recipientCount: number }
}
export interface KnowledgeGrant { userId: string; user?: { displayName?: string; username?: string }; grantedByUserId: string; grantedBy?: { displayName?: string; username?: string }; canReshare: boolean; status: string }

function authHeaders(token?: string | null): Record<string, string> {
  const resolved = token ?? (typeof window !== 'undefined' ? window.localStorage.getItem('auth_token') : '')
  return resolved ? { Authorization: `Bearer ${resolved}` } : {}
}

async function readJson<T>(url: string, options: RequestInit = {}, token?: string | null): Promise<T> {
  const resp = await fetch(url, { ...options, headers: { ...authHeaders(token), ...(options.headers || {}) } })
  notifyAuthExpiredFromResponse(resp, Boolean(token))
  if (!resp.ok) throw new Error(`Request failed: ${resp.status}`)
  return resp.json() as Promise<T>
}

function qs(params: Record<string, string | number>): string {
  return new URLSearchParams(Object.entries(params).map(([k, v]) => [k, String(v)])) .toString()
}

// ---------------------------------------------------------------------------
// 目录
// ---------------------------------------------------------------------------

export async function fetchKnowledgeDirectories() {
  const data = await readJson<{ items: KnowledgeDirectory[]; rootCount: number }>('/api/knowledge/directories')
  return data
}

export async function createKnowledgeDirectory(name: string, parentId = ''): Promise<unknown> {
  return readJson('/api/knowledge/directories', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name, parentId }) })
}

export async function updateKnowledgeDirectory(id: string, name: string, parentId = ''): Promise<unknown> {
  return readJson(`/api/knowledge/directories/${encodeURIComponent(id)}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name, parentId }) })
}

export async function deleteKnowledgeDirectory(id: string): Promise<unknown> {
  return readJson(`/api/knowledge/directories/${encodeURIComponent(id)}`, { method: 'DELETE' })
}

// ---------------------------------------------------------------------------
// 文档列表 / 详情
// ---------------------------------------------------------------------------

export async function fetchPersonalKnowledge(view: 'mine' | 'shared', directoryId = 'all', keyword = '', page = 1, pageSize = 12) {
  const data = await readJson<{ items: PersonalKnowledge[]; total: number; page: number; pageSize: number }>(
    `/api/knowledge/documents?${qs({ view, directoryId, keyword, page, pageSize })}`,
  )
  return data
}

export async function fetchPersonalKnowledgeItem(id: string): Promise<PersonalKnowledge> {
  return readJson<PersonalKnowledge>(`/api/knowledge/documents/${encodeURIComponent(id)}`)
}

export async function searchSelectablePersonalKnowledge(
  view: 'mine' | 'shared', directoryId = 'all', keyword = '', page = 1, pageSize = 20,
) {
  const data = await readJson<{ items: PersonalKnowledge[]; total: number; page: number; pageSize: number }>(
    `/api/knowledge/documents?${qs({ view, directoryId, keyword, page, pageSize })}`,
  )
  return data
}

export async function fetchSelectablePersonalKnowledgeItem(id: string): Promise<PersonalKnowledge> {
  return fetchPersonalKnowledgeItem(id)
}

// ---------------------------------------------------------------------------
// 上传 / 更新 / 删除 / 重建 / 替换
// ---------------------------------------------------------------------------

export async function uploadPersonalKnowledge(files: File[], directoryId = '', tags = '', onProgress?: (value: number) => void): Promise<{ items: PersonalKnowledge[] }> {
  const items: PersonalKnowledge[] = []
  const total = files.length
  let done = 0
  for (const file of files) {
    const form = new FormData()
    form.append('file', file)
    form.append('directoryId', directoryId)
    form.append('tags', tags)
    const resp = await fetch('/api/knowledge/documents', { method: 'POST', headers: authHeaders(), body: form })
    notifyAuthExpiredFromResponse(resp, true)
    if (!resp.ok) {
      const detail = await resp.text()
      throw new Error(detail || `Upload failed: ${resp.status}`)
    }
    const created = (await resp.json()) as { id: string; name: string; status: string }
    items.push({
      id: created.id, ownerUserId: '', directoryId, name: created.name, description: '', tags: [],
      status: created.status, error: '', activeDocumentId: created.id, canReshare: false,
      accessStatus: 'owner', grantedByUserId: '', seen: true, sharedAt: '', createdAt: '', updatedAt: '',
      isOwner: true, canShare: false, share: { shared: false, recipientCount: 0 },
    })
    done += 1
    onProgress?.(Math.round((done / Math.max(1, total)) * 100))
  }
  return { items }
}

export async function updatePersonalKnowledge(id: string, payload: Partial<Pick<PersonalKnowledge, 'name' | 'description' | 'tags' | 'directoryId'>>): Promise<PersonalKnowledge> {
  return readJson<PersonalKnowledge>(`/api/knowledge/documents/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: payload.name, description: payload.description, tags: payload.tags, directoryId: payload.directoryId }),
  })
}

export async function relearnPersonalKnowledge(id: string): Promise<unknown> {
  return readJson(`/api/knowledge/documents/${encodeURIComponent(id)}/relearn`, { method: 'POST' })
}

export async function replacePersonalKnowledge(id: string, file: File): Promise<unknown> {
  const form = new FormData()
  form.append('file', file)
  const resp = await fetch(`/api/knowledge/documents/${encodeURIComponent(id)}/replace`, { method: 'POST', headers: authHeaders(), body: form })
  notifyAuthExpiredFromResponse(resp, true)
  if (!resp.ok) throw new Error(`Replace failed: ${resp.status}`)
  return resp.json()
}

export async function deletePersonalKnowledge(id: string): Promise<unknown> {
  return readJson(`/api/knowledge/documents/${encodeURIComponent(id)}`, { method: 'DELETE' })
}

// ---------------------------------------------------------------------------
// 分享 / 授权（已接真 knowledge_shares）；协同编辑属后续阶段
// ---------------------------------------------------------------------------

export async function dismissDeletedPersonalKnowledge(_id: string): Promise<unknown> {
  return { ok: true }
}

export interface KnowledgeAskCitation {
  index: number
  chunkId: string
  documentId: string
  documentTitle: string
  headingPath: string[]
  score: number
  text: string
}

export interface KnowledgeAskResult { answer: string; citations: KnowledgeAskCitation[]; usedChunks: string[] }

export async function askPersonalKnowledge(id: string, query: string): Promise<KnowledgeAskResult> {
  // 问答接真：POST /api/knowledge/documents/{id}/ask（knowledge_user.py，混合检索 + 提供商生成）。
  // 错误形状对齐 sharePersonalKnowledge 契约：error.response.data.detail = 后端 detail 文案，
  // 让调用方能透出「问题不能为空」「问答生成失败: …」等明确提示，而非裸状态码。
  const resp = await fetch(`/api/knowledge/documents/${encodeURIComponent(id)}/ask`, {
    method: 'POST',
    headers: { ...authHeaders(), 'Content-Type': 'application/json' },
    body: JSON.stringify({ query }),
  })
  notifyAuthExpiredFromResponse(resp, true)
  const body = await resp.json().catch(() => null) as KnowledgeAskResult | null
  if (!resp.ok) {
    throw Object.assign(new Error(`Ask failed: ${resp.status}`), {
      response: { status: resp.status, data: body },
    })
  }
  return body as KnowledgeAskResult
}

export async function fetchKnowledgeGrants(id: string) {
  return readJson<{ items: KnowledgeGrant[] }>(`/api/knowledge/documents/${encodeURIComponent(id)}/grants`)
}

export async function sharePersonalKnowledge(id: string, recipients: Array<{ userId: string; canReshare: boolean }>): Promise<unknown> {
  // 直分享接真：POST /api/knowledge/documents/{id}/share（knowledge_user.py，owner 或 canReshare 授权者）。
  // 错误形状对齐 KnowledgeShareDialog 契约：error.response.data.detail = 后端 detail 文案。
  const resp = await fetch(`/api/knowledge/documents/${encodeURIComponent(id)}/share`, {
    method: 'POST',
    headers: { ...authHeaders(), 'Content-Type': 'application/json' },
    body: JSON.stringify({ recipients }),
  })
  notifyAuthExpiredFromResponse(resp, true)
  const body = await resp.json().catch(() => null)
  if (!resp.ok) {
    throw Object.assign(new Error(`Share failed: ${resp.status}`), {
      response: { status: resp.status, data: body },
    })
  }
  return body
}

export async function revokePersonalKnowledge(id: string, userId: string, _cascade = false): Promise<unknown> {
  return readJson(`/api/knowledge/documents/${encodeURIComponent(id)}/grants/${encodeURIComponent(userId)}`, { method: 'DELETE' })
}

export async function updatePersonalKnowledgeGrant(id: string, userId: string, canReshare: boolean): Promise<unknown> {
  return readJson(`/api/knowledge/documents/${encodeURIComponent(id)}/grants/${encodeURIComponent(userId)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ canReshare }),
  })
}

export async function fetchPersonalKnowledgeCounts() {
  return readJson<{ unreadCount: number; shareCount: number; receivedCount?: number; feedbackCount: number }>('/api/knowledge/counts')
}
