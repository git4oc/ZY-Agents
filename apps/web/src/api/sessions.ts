import axios from 'axios'

// ZY-Agents 主服务：会话即 conversation（/api/v1/conversations）。
// 契约：响应为裸 dict；列表为 {items: [...], next_cursor}；
// 消息列表为 {items: [{id, seq, role, content: [{type:'text', data:{text}}], meta, created_at}]}。
const client = axios.create({
  baseURL: '/api/v1',
  timeout: 120000,
})

export type ChatMessage = {
  role: string
  content: string
  plan?: any
  progress?: { content: string; timestamp?: string }[]
  documents?: DocumentInfo[]
  images?: ImageInfo[]
  /** 服务端为本次助手回复签发的 id。 */
  message_id?: string
  /** 消息作者——user_id 即作者。 */
  user_id?: string
  /** 会话历史返回的已持久化 V3 事件，用于回放。 */
  execution_events?: any[]
  trigger_source?: string
  scheduled_job_id?: string
  scheduled_run_id?: string
  created_at?: string
}

export type DocumentInfo = {
  type: 'pdf' | 'docx' | 'ppt' | 'pptx' | 'md' | 'xlsx'
  url: string
  filename?: string
  title?: string
  object_path?: string
}

export type ImageInfo = {
  object_path?: string
  url?: string
  signed_url?: string
  filename?: string
  content_type?: string
  size?: number
}

export type SessionSummary = {
  id: string
  user_id: string
  title: string
  created_at: string
  updated_at: string
  last_message_at?: string | null
  last_message_preview?: string | null
  message_count: number
  active_run?: {
    run_id: string
    message_id: string
    source: 'scheduled' | string
    status: 'running' | 'suspended' | string
    initiator_user_id?: string
    scheduled_job_id?: string
    started_at?: string
  } | null
  scheduled_unread?: boolean
  pending_approval_count?: number
  last_scheduled_run?: { run_id: string; status: string; finished_at?: string } | null
  access?: 'owner' | 'shared'
  owner_user_id?: string
  participant_count?: number
  shared_unread?: boolean
  execution_location?: 'server' | 'desktop' | 'remote_sandbox'
  runtime_preset_id?: string
  model_instance_id?: string | null
  code_project?: { workspace_id: string; git_branch: string; worktree: boolean } | null
}

export type SessionDetail = SessionSummary & {
  messages: ChatMessage[]
  access: 'owner' | 'shared'
  owner_user_id: string
  participant_count: number
}

export type SessionSearchResult = SessionSummary & {
  match_type: 'title' | 'preview' | 'message'
  snippets: string[]
  matched_message_count: number
}

export type SessionListPage = {
  items: SessionSummary[]
  offset: number
  limit: number
  has_more: boolean
}

export type SessionSearchPage = {
  items: SessionSearchResult[]
  offset: number
  limit: number
  has_more: boolean
}

function authHeaders(authToken?: string | null) {
  return authToken ? { Authorization: `Bearer ${authToken}` } : {}
}

/** ZY 会话载荷 → SessionSummary。 */
function convToSummary(raw: any): SessionSummary {
  return {
    id: String(raw?.id || ''),
    user_id: String(raw?.owner_user_id || ''),
    title: String(raw?.title || 'New Chat'),
    created_at: String(raw?.created_at || ''),
    updated_at: String(raw?.last_active_at || raw?.created_at || ''),
    message_count: 0,
    active_run: raw?.active_run
      ? {
          run_id: String(raw.active_run.run_id || ''),
          message_id: String(raw.active_run.request_id || raw.active_run.message_id || ''),
          source: 'server',
          status: String(raw.active_run.status || 'running'),
        }
      : null,
    access: 'owner',
    owner_user_id: String(raw?.owner_user_id || ''),
    participant_count: 1,
    execution_location: 'server',
  }
}

/** ZY message payload（content 为分段数组）→ ChatMessage。 */
function messageFromItem(raw: any): ChatMessage {
  const parts = Array.isArray(raw?.content) ? raw.content : []
  const text = parts
    .map((part: any) => (part?.type === 'text' ? String(part?.data?.text ?? '') : ''))
    .filter(Boolean)
    .join('\n')
  return {
    role: raw?.role === 'user' ? 'user' : 'assistant',
    content: text,
    message_id: raw?.id ? String(raw.id) : undefined,
    created_at: raw?.created_at,
  }
}

async function fetchConversationMessages(
  conversationId: string,
  authToken?: string | null,
): Promise<ChatMessage[]> {
  const response = await client.get(
    `/conversations/${encodeURIComponent(conversationId)}/messages`,
    { headers: authHeaders(authToken) },
  )
  const items = Array.isArray(response.data?.items) ? response.data.items : []
  return items.map(messageFromItem)
}

export async function listSessions(_userId: string, _mainId?: string, authToken?: string | null): Promise<SessionSummary[]> {
  const response = await client.get('/conversations', {
    timeout: 15000, headers: authHeaders(authToken),
  })
  const items = Array.isArray(response.data?.items) ? response.data.items : []
  return items.map(convToSummary)
}

export async function listSessionsPaged(
  _userId: string,
  _mainId?: string,
  options: { limit?: number; offset?: number; scope?: 'owner' | 'shared' } = {},
  authToken?: string | null,
): Promise<SessionListPage> {
  const limit = options.limit ?? 30
  const offset = options.offset ?? 0
  const response = await client.get('/conversations', {
    timeout: 15000, headers: authHeaders(authToken),
  })
  const items = Array.isArray(response.data?.items) ? response.data.items : []
  return {
    items: items.map(convToSummary),
    offset,
    limit,
    // ZY 后端为 cursor 分页：有 next_cursor 即还有更多。
    has_more: Boolean(response.data?.next_cursor),
  }
}

/** ZY-Agents 阶段 1 无搜索端点：返回空结果（搜索框自然空态）。 */
export async function searchSessions(
  _userId: string,
  _mainId: string | undefined,
  _queryText: string,
  options: { limit?: number; offset?: number } = {},
  _authToken?: string | null,
): Promise<SessionSearchPage> {
  return {
    items: [],
    offset: options.offset ?? 0,
    limit: options.limit ?? 20,
    has_more: false,
  }
}

export async function createSession(
  _userId: string,
  _mainId?: string,
  title?: string,
  _messages?: ChatMessage[],
  authToken?: string | null,
): Promise<SessionSummary> {
  const response = await client.post('/conversations', {
    title: title || 'New Chat',
  }, { headers: authHeaders(authToken) })
  return convToSummary(response.data)
}

export async function getSession(
  sessionId: string,
  _userId: string,
  _mainId?: string,
  authToken?: string | null,
): Promise<SessionDetail> {
  const response = await client.get(`/conversations/${encodeURIComponent(sessionId)}`, {
    headers: authHeaders(authToken),
  })
  const messages = await fetchConversationMessages(sessionId, authToken)
  return {
    ...convToSummary(response.data),
    messages,
    access: 'owner',
    owner_user_id: String(response.data?.owner_user_id || ''),
    participant_count: 1,
  }
}

export async function deleteSession(
  sessionId: string,
  _userId: string,
  _mainId?: string,
  authToken?: string | null,
): Promise<{ id: string }> {
  await client.delete(`/conversations/${encodeURIComponent(sessionId)}`, {
    headers: authHeaders(authToken),
  })
  return { id: sessionId }
}

export async function updateSessionTitle(
  sessionId: string,
  _userId: string,
  _mainId: string | undefined,
  title: string,
  authToken?: string | null,
): Promise<SessionSummary> {
  const response = await client.patch(`/conversations/${encodeURIComponent(sessionId)}`, {
    title,
  }, { headers: authHeaders(authToken) })
  return convToSummary(response.data)
}

/** ZY 后端消息由 turn 落库，无独立 append 端点：返回最新会话快照。 */
export async function appendMessages(
  sessionId: string,
  _userId: string,
  _mainId: string | undefined,
  _messages: ChatMessage[],
  authToken?: string | null,
): Promise<SessionSummary> {
  const response = await client.get(`/conversations/${encodeURIComponent(sessionId)}`, {
    headers: authHeaders(authToken),
  })
  return convToSummary(response.data)
}

/** ZY-Agents 无 desktop project 端点：占位空列表。 */
export type DesktopProject = {
  workspace_id: string
  title: string
  worktree: boolean
  created_at: string
  updated_at: string
}

export async function createDesktopProject(
  payload: Pick<DesktopProject, 'workspace_id' | 'title' | 'worktree'>,
  _authToken?: string | null,
): Promise<DesktopProject> {
  const now = new Date().toISOString()
  return { ...payload, created_at: now, updated_at: now }
}

export async function listDesktopProjects(_authToken?: string | null): Promise<DesktopProject[]> {
  return []
}
