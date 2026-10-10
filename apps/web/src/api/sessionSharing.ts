// ZY-Agents 会话分享：token 链接语义（conversations.py 会话分享段 + participants）。
//
// 闭环：owner 创建分享链接（POST /shares，快照 participant_count + token）→
// 接收者按 token 加入（POST /session-shares/{token}/join，落 session_participants
// role=participant）→ 使用 = 参与者对消息 / SSE / 产物的访问权（collaboration/
// 参与者校验内建）。吊销（DELETE /shares）后 join 立即拒绝。
//
// 契约：Result 形状（ok/data | ok=false/error），error = {code, status, message}；
// 后端 detail 文案优先透出，401 时 notifyAuthExpiredFromResponse 已派发登录过期
// 事件（App.vue join 流依赖 status===401 保留 token 待重登重试）。

import { notifyAuthExpiredFromResponse } from './authExpiry'
import { extractErrorMessage } from './errorMessage'

function dataOfError(code: SessionShareErrorCode, message: string) {
  return { code, status: 501, message } as const
}

const SESSION_SHARE_ERROR_CODES = [
  'session_not_found',
  'session_share_not_found',
  'session_share_owner_required',
  'session_share_participant_required',
  'session_share_no_binding',
  'session_share_not_server',
  'session_share_inactive',
  'session_share_token_required',
  'http_error',
] as const

export type SessionShareErrorCode = (typeof SESSION_SHARE_ERROR_CODES)[number]

export type SessionShareError = {
  code: SessionShareErrorCode
  status: number
  message: string
}

export type SessionShareResult<T> =
  | { ok: true; data: T }
  | { ok: false; error: SessionShareError }

export interface SessionShareCreated {
  /** 原始分享令牌：仅展示一次，且只写入剪贴板或链接输入框。 */
  token: string
  expires_at: string
  participant_count: number
}

export interface SessionShareJoined {
  session_id: string
}

export interface SessionShareMember {
  user_id: string
  display_name: string
  role: 'owner' | 'participant'
  joined_at: string | null
}

export interface SessionShareMemberPage {
  items: SessionShareMember[]
}

export interface SessionShareMutation {
  session_id: string
  user_id?: string
}

function authHeaders(token?: string | null): Record<string, string> {
  const resolved = token ?? (typeof window !== 'undefined' ? window.localStorage.getItem('auth_token') : '')
  return resolved ? { Authorization: `Bearer ${resolved}` } : {}
}

/** HTTP 状态码 → 前端错误码（对齐 SessionShareDialog 的错误文案映射）。 */
function mapErrorCode(status: number, fallback: SessionShareErrorCode): SessionShareErrorCode {
  if (status === 404) return 'session_share_not_found'
  if (status === 403) return 'session_share_owner_required'
  if (status === 401) return 'session_share_inactive'
  if (status === 501) return 'session_share_not_server'
  return fallback
}

async function request<T>(
  url: string,
  options: RequestInit,
  fallbackCode: SessionShareErrorCode,
): Promise<SessionShareResult<T>> {
  const resp = await fetch(url, { ...options, headers: { ...authHeaders(), ...(options.headers || {}) } })
  notifyAuthExpiredFromResponse(resp, true)
  const body = await resp.json().catch(() => null)
  if (!resp.ok) {
    return {
      ok: false,
      error: {
        code: mapErrorCode(resp.status, fallbackCode),
        status: resp.status,
        message: extractErrorMessage(body, `session share request failed: ${resp.status}`),
      },
    }
  }
  return { ok: true, data: body as T }
}

export async function createSessionShare(sessionId: string, expiresInDays: number): Promise<SessionShareResult<SessionShareCreated>> {
  if (!sessionId) return { ok: false, error: dataOfError('session_share_token_required', 'session id is required') }
  return request<SessionShareCreated>(
    `/api/v1/conversations/${encodeURIComponent(sessionId)}/shares`,
    { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ expires_in_days: expiresInDays }) },
    'http_error',
  )
}

export async function revokeSessionShare(sessionId: string): Promise<SessionShareResult<SessionShareMutation>> {
  if (!sessionId) return { ok: false, error: dataOfError('session_share_token_required', 'session id is required') }
  return request<SessionShareMutation>(
    `/api/v1/conversations/${encodeURIComponent(sessionId)}/shares`,
    { method: 'DELETE' },
    'http_error',
  )
}

export async function joinSessionShare(token: string): Promise<SessionShareResult<SessionShareJoined>> {
  if (!token) return { ok: false, error: dataOfError('session_share_token_required', 'share token is required') }
  return request<SessionShareJoined>(
    `/api/v1/session-shares/${encodeURIComponent(token)}/join`,
    { method: 'POST' },
    'session_share_not_found',
  )
}

export async function listSessionParticipants(sessionId: string): Promise<SessionShareResult<SessionShareMemberPage>> {
  if (!sessionId) return { ok: true, data: { items: [] } }
  return request<SessionShareMemberPage>(
    `/api/v1/conversations/${encodeURIComponent(sessionId)}/participants`,
    { method: 'GET' },
    'http_error',
  )
}

export async function leaveSession(sessionId: string): Promise<SessionShareResult<SessionShareMutation>> {
  if (!sessionId) return { ok: false, error: dataOfError('session_share_token_required', 'session id is required') }
  return request<SessionShareMutation>(
    `/api/v1/conversations/${encodeURIComponent(sessionId)}/leave`,
    { method: 'POST' },
    'http_error',
  )
}

export async function removeParticipant(sessionId: string, userId: string): Promise<SessionShareResult<SessionShareMutation>> {
  if (!sessionId || !userId) return { ok: false, error: dataOfError('session_share_token_required', 'ids are required') }
  return request<SessionShareMutation>(
    `/api/v1/conversations/${encodeURIComponent(sessionId)}/participants/${encodeURIComponent(userId)}`,
    { method: 'DELETE' },
    'http_error',
  )
}
