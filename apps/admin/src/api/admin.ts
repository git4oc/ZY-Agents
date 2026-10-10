import { http } from './client'

/** 后端多数列表端点直接返回 mongo 原始文档。 */
export type AdminDoc = Record<string, unknown>

function itemsOf(raw: unknown): AdminDoc[] {
  const data = raw as { items?: unknown } | null
  return Array.isArray(data?.items) ? (data.items as AdminDoc[]) : []
}

export function docId(doc: AdminDoc): string {
  const id = doc._id ?? doc.id ?? doc.device_id ?? doc.tool_id ?? doc.skill_id
  return id === undefined || id === null ? '' : String(id)
}

export function docText(doc: AdminDoc, key: string): string {
  const value = doc[key]
  if (value === undefined || value === null) return '—'
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  return JSON.stringify(value)
}

/** 统一的时间格式化：YYYY-MM-DD HH:mm（本地时区）。ISO 毫秒串直出难读，全站一个口径。 */
export function fmtTime(value: unknown): string {
  if (value === undefined || value === null || value === '') return '—'
  const date = new Date(String(value))
  if (Number.isNaN(date.getTime())) return String(value)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

// ---------------- 组织单元 ----------------

export type OrgUnitInput = { name: string; code: string; parent_id?: string | null }

export async function listOrgUnits(): Promise<AdminDoc[]> {
  const response = await http.get('/admin/org-units')
  return itemsOf(response.data)
}

export async function createOrgUnit(input: OrgUnitInput): Promise<{ id: string }> {
  const response = await http.post('/admin/org-units', input)
  const data = (response.data || {}) as { id?: string }
  return { id: String(data.id ?? '') }
}

// ---------------- 用户 ----------------

export type AdminUserRow = {
  id: string
  username: string
  display_name: string
  status: string
}

export async function listUsers(): Promise<AdminUserRow[]> {
  const response = await http.get('/admin/users')
  return itemsOf(response.data).map((row) => ({
    id: String(row.id ?? ''),
    username: String(row.username ?? ''),
    display_name: String(row.display_name ?? ''),
    status: String(row.status ?? ''),
  }))
}

// ---------------- 工具 ----------------

/**
 * 工具创建入参（对齐后端 `ToolCreate`）。
 * `name` 是唯一索引 `tools_org_name_u = (org_unit_id, name)` 的组成部分，**必填**。
 */
export type ToolInput = {
  name: string
  description?: string
  type?: string
  category?: string
  risk_level?: string
  approval_required?: boolean
  timeout_ms?: number
  status?: string
}

export async function listTools(): Promise<AdminDoc[]> {
  const response = await http.get('/admin/tools')
  return itemsOf(response.data)
}

export async function createTool(payload: ToolInput): Promise<{ id: string }> {
  const response = await http.post('/admin/tools', payload)
  const data = (response.data || {}) as { id?: string }
  return { id: String(data.id ?? '') }
}

export async function patchTool(toolId: string, payload: Partial<ToolInput>): Promise<void> {
  await http.patch(`/admin/tools/${encodeURIComponent(toolId)}`, payload)
}

// ---------------- 技能 ----------------

/**
 * 技能创建入参（对齐后端 `SkillCreate`）。
 * `key` 是唯一索引 `skills_org_key_u = (org_unit_id, key)` 的组成部分，**必填**
 * （缺失后端返回 422）。`name` 只是展示名。
 */
export type SkillInput = {
  key: string
  name: string
  description?: string
  kind?: string
  status?: string
}

export async function listSkills(): Promise<AdminDoc[]> {
  const response = await http.get('/admin/skills')
  return itemsOf(response.data)
}

export async function createSkill(payload: SkillInput): Promise<{ id: string }> {
  const response = await http.post('/admin/skills', payload)
  const data = (response.data || {}) as { id?: string }
  return { id: String(data.id ?? '') }
}

/**
 * 技能详情投影（对齐后端 `_skill_jsonable`）。
 *
 * ⚠️ `enabled` 与 mongo 里的 `status` 是**后端做的双向映射**
 * （`status === 'active'` ⇔ `enabled === true`），前端一律用 `enabled`，
 * 不要自己拼 `status`——那是后端写端点唯一接受的开关字段。
 *
 * ⚠️ 读取端点**放宽为登录用户**即可（写操作才要 `skill.manage` 守卫），
 * 与 `list_skills` 同口径。所以 member 也能打开详情抽屉，只是保存会 403。
 */
export type SkillDetail = {
  id: string
  mainId: string
  key: string
  name: string
  description: string
  scenario: string
  type: string
  config: Record<string, unknown>
  enabled: boolean
  createdAt: string
  updatedAt: string
}

/** PATCH 可写字段（对齐后端 `SkillPatch`，`extra="ignore"` 多余字段会被丢弃）。 */
export type SkillPatchInput = {
  name?: string
  description?: string
  scenario?: string
  type?: string
  config?: Record<string, unknown>
  enabled?: boolean
}

export async function fetchSkill(skillId: string): Promise<SkillDetail> {
  const response = await http.get(`/admin/skills/${encodeURIComponent(skillId)}`)
  const row = (response.data || {}) as Record<string, unknown>
  return {
    id: String(row.id ?? skillId),
    mainId: String(row.mainId ?? ''),
    key: String(row.key ?? ''),
    name: String(row.name ?? ''),
    description: String(row.description ?? ''),
    scenario: String(row.scenario ?? ''),
    type: String(row.type ?? 'ordinary'),
    config: (row.config ?? {}) as Record<string, unknown>,
    enabled: row.enabled === true,
    createdAt: String(row.createdAt ?? ''),
    updatedAt: String(row.updatedAt ?? ''),
  }
}

export async function patchSkill(skillId: string, payload: SkillPatchInput): Promise<void> {
  await http.patch(`/admin/skills/${encodeURIComponent(skillId)}`, payload)
}

export async function deleteSkill(skillId: string): Promise<void> {
  await http.delete(`/admin/skills/${encodeURIComponent(skillId)}`)
}

// ---------------- 模型供应商 / 实例 ----------------

/**
 * 模型供应商可写字段。**与后端字段白名单严格对齐**（`services/api/app/api/endpoints/admin.py`
 * 的 `ModelProviderCreate`），并与唯一索引 `model_providers_org_code_u = (org_unit_id, code)`
 * 对齐——`name` / `code` 是**必填**（后端 Pydantic 校验，缺失直接 422）。
 *
 * ⚠️ 刻意**不提供** `api_key_enc`：密钥不经管理端写端点落库。
 */
export type ModelProviderInput = {
  name: string
  code: string
  base_url: string
  provider_type: string
  status?: string
  settings?: Record<string, unknown>
}

export async function listModelProviders(): Promise<AdminDoc[]> {
  const response = await http.get('/admin/model-providers')
  return itemsOf(response.data)
}

export async function createModelProvider(input: ModelProviderInput): Promise<{ id: string }> {
  const response = await http.post('/admin/model-providers', input)
  const data = (response.data || {}) as { id?: string }
  return { id: String(data.id ?? '') }
}

export async function patchModelProvider(
  providerId: string,
  payload: Partial<ModelProviderInput>,
): Promise<void> {
  await http.patch(`/admin/model-providers/${encodeURIComponent(providerId)}`, payload)
}

/**
 * 模型实例可写字段（对齐后端 `ModelInstanceCreate`）。
 * `provider_id` / `model_name` 是唯一索引 `model_instances_u = (org_unit_id, provider_id, model_name)`
 * 的组成部分，**必填**。
 */
export type ModelInstanceInput = {
  provider_id: string
  model_name: string
  display_name: string
  capabilities: string[]
  max_context_tokens: number
  status?: string
  settings?: Record<string, unknown>
}

export async function listModelInstances(): Promise<AdminDoc[]> {
  const response = await http.get('/admin/model-instances')
  return itemsOf(response.data)
}

export async function createModelInstance(input: ModelInstanceInput): Promise<{ id: string }> {
  const response = await http.post('/admin/model-instances', input)
  const data = (response.data || {}) as { id?: string }
  return { id: String(data.id ?? '') }
}

export async function patchModelInstance(
  instanceId: string,
  payload: Partial<ModelInstanceInput>,
): Promise<void> {
  await http.patch(`/admin/model-instances/${encodeURIComponent(instanceId)}`, payload)
}

// ---------------- 角色绑定 / 资源授权 ----------------

/** 资源授权 / 角色绑定共用的资源类型选项（对齐后端 acl 资源类型，两页口径必须一致）。 */
export const RESOURCE_TYPE_OPTIONS = [
  { label: 'tool（工具）', value: 'tool' },
  { label: 'skill（技能）', value: 'skill' },
  { label: 'model_instance（模型实例）', value: 'model_instance' },
  { label: 'knowledge_base（知识库）', value: 'knowledge_base' },
  { label: 'device（设备）', value: 'device' },
]

/** 角色绑定可写字段（对齐后端 `_BINDING_WRITABLE`）。 */
export type RoleBindingInput = {
  subject_type: 'user' | 'role'
  subject_id: string
  role_code: string
  scope: 'org_unit' | 'resource'
  resource_type?: string | null
  resource_id?: string | null
}

export async function listRoleBindings(): Promise<AdminDoc[]> {
  const response = await http.get('/admin/role-bindings')
  return itemsOf(response.data)
}

export async function createRoleBinding(input: RoleBindingInput): Promise<{ id: string }> {
  const response = await http.post('/admin/role-bindings', input)
  const data = (response.data || {}) as { id?: string }
  return { id: String(data.id ?? '') }
}

export async function patchRoleBinding(
  bindingId: string,
  payload: Partial<RoleBindingInput>,
): Promise<void> {
  await http.patch(`/admin/role-bindings/${encodeURIComponent(bindingId)}`, payload)
}

/**
 * 资源授权可写字段（对齐后端 `_GRANT_WRITABLE`）。
 *
 * ⚠️ `effect` 后端**只接受** `allow` / `deny`，其它值会被 422 拒绝；
 * 且 `deny` **优先**——存在任一 deny 即拒绝，不会因为还有一条 allow 就放过。
 * `subject_type: 'all'` 时 `subject_id` 留空（后端存 null）。
 */
export type GrantInput = {
  resource_type: string
  resource_id: string
  subject_type: 'user' | 'role' | 'all'
  subject_id?: string | null
  effect: 'allow' | 'deny'
}

export async function listGrants(): Promise<AdminDoc[]> {
  const response = await http.get('/admin/grants')
  return itemsOf(response.data)
}

export async function createGrant(input: GrantInput): Promise<{ id: string }> {
  const response = await http.post('/admin/grants', input)
  const data = (response.data || {}) as { id?: string }
  return { id: String(data.id ?? '') }
}

export async function patchGrant(grantId: string, payload: Partial<GrantInput>): Promise<void> {
  await http.patch(`/admin/grants/${encodeURIComponent(grantId)}`, payload)
}

// ---------------- 设备 ----------------

export async function listDevices(): Promise<AdminDoc[]> {
  const response = await http.get('/admin/devices')
  return itemsOf(response.data)
}

/** DELETE /admin/devices/{device_id} —— 后端把该设备置为 revoked。 */
export async function revokeDevice(deviceId: string): Promise<void> {
  await http.delete(`/admin/devices/${encodeURIComponent(deviceId)}`)
}

// ---------------- 审计 ----------------

export type AuditQuery = { action?: string; limit?: number; offset?: number }

export async function queryAudit(query: AuditQuery): Promise<{ items: AdminDoc[]; total: number }> {
  const response = await http.get('/admin/audit', {
    params: {
      ...(query.action ? { action: query.action } : {}),
      limit: query.limit ?? 50,
      offset: query.offset ?? 0,
    },
  })
  const data = (response.data || {}) as { items?: unknown; total?: unknown }
  return {
    items: Array.isArray(data.items) ? (data.items as AdminDoc[]) : [],
    total: Number(data.total ?? 0),
  }
}

// ---------------- 配额 ----------------

export type QuotaUsage = {
  model_tokens_prompt: number
  model_tokens_completion: number
  tool_calls: number
}

export async function fetchQuotaUsage(): Promise<QuotaUsage> {
  const response = await http.get('/admin/quota')
  const data = (response.data || {}) as Partial<QuotaUsage>
  return {
    model_tokens_prompt: Number(data.model_tokens_prompt ?? 0),
    model_tokens_completion: Number(data.model_tokens_completion ?? 0),
    tool_calls: Number(data.tool_calls ?? 0),
  }
}

// ---------------- 知识库（管理端）----------------

/**
 * 管理端知识文档行（对齐后端 `knowledge_admin.list_documents` 的投影）。
 *
 * 后端只投影这 8 个字段，**不返回** `storage_path` / `org_unit_id` 等内部字段——
 * 管理端因此拿不到原件落盘路径，也无法自行拼预览 URL，预览须走后端
 * `/api/v1/knowledge/sources/documents/{id}/preview`。
 */
export type KnowledgeDocument = {
  id: string
  title: string
  original_filename: string
  file_ext: string
  /** 解析状态（processing / ready / failed …），空串表示后端未记录该字段 */
  status: string
  chunk_count: number
  error: string
}

/** GET /admin/knowledge/documents —— 全 org 文档列表（含解析状态）。 */
export async function listKnowledgeDocuments(): Promise<KnowledgeDocument[]> {
  const response = await http.get('/admin/knowledge/documents')
  return itemsOf(response.data).map((row) => ({
    id: String(row.id ?? ''),
    title: String(row.title ?? ''),
    original_filename: String(row.original_filename ?? ''),
    file_ext: String(row.file_ext ?? ''),
    status: String(row.status ?? ''),
    chunk_count: Number(row.chunk_count ?? 0),
    error: String(row.error ?? ''),
  }))
}

/** DELETE /admin/knowledge/documents/{document_id} —— 原件 + chunks + 记录一并清理。 */
export async function deleteKnowledgeDocument(documentId: string): Promise<void> {
  await http.delete(`/admin/knowledge/documents/${encodeURIComponent(documentId)}`)
}

/** 后端 `_SUPPORTED` 允许的上传类型（`knowledge_admin.upload_document` 会硬校验）。 */
export const KNOWLEDGE_UPLOAD_EXTENSIONS = ['.pdf', '.docx', '.txt', '.md'] as const

export type KnowledgeUploadResult = {
  id: string
  title: string
  /** 后端是**异步索引**：先回 processing，后台 task 完成后再改。 */
  status: string
  chunk_count: number
}

/**
 * POST /admin/knowledge/documents —— multipart 上传（`file` 必填，`title` 可选）。
 *
 * 需 `_guard`：admin 角色直通，或显式 `knowledge.manage`。因此 `ops` 角色会 403。
 * 返回即代表**原件已落盘**，不代表索引已完成——解析在后台 task 里跑。
 */
export async function uploadKnowledgeDocument(
  file: File,
  title = '',
): Promise<KnowledgeUploadResult> {
  const form = new FormData()
  form.append('file', file)
  if (title.trim()) form.append('title', title.trim())
  const response = await http.post('/admin/knowledge/documents', form)
  const data = (response.data || {}) as Record<string, unknown>
  return {
    id: String(data.id ?? ''),
    title: String(data.title ?? ''),
    status: String(data.status ?? ''),
    chunk_count: Number(data.chunk_count ?? 0),
  }
}

/**
 * 文档元信息（`GET /sources/documents/{id}`）。
 *
 * ⚠️ 这里用的是 **sources 端点而不是 `GET /knowledge/documents/{id}`**，因为：
 * - `_load_document` 只做 org 隔离 → 管理端能看**全 org** 任何人的文档
 * - `_load_visible`（用户端详情/共享）要校验 owner 或 active grant → 管理端看别人的文档会 404
 *
 * 代价：拿不到用户端独有的字段（shareCount / accessStatus 等）。
 */
export type KnowledgeSourceMeta = {
  id: string
  scope: string
  name: string
  originalFilename: string
  fileExt: string
  mimeType: string
  previewMimeType: string
  previewStatus: string
  chunkCount: number
  canDownload: boolean
}

export async function fetchKnowledgeSourceMeta(documentId: string): Promise<KnowledgeSourceMeta> {
  const response = await http.get(`/sources/documents/${encodeURIComponent(documentId)}`)
  const row = (response.data || {}) as Record<string, unknown>
  return {
    id: String(row.id ?? documentId),
    scope: String(row.scope ?? ''),
    name: String(row.name ?? ''),
    originalFilename: String(row.originalFilename ?? ''),
    fileExt: String(row.fileExt ?? ''),
    mimeType: String(row.mimeType ?? ''),
    previewMimeType: String(row.previewMimeType ?? ''),
    previewStatus: String(row.previewStatus ?? ''),
    chunkCount: Number(row.chunkCount ?? 0),
    canDownload: row.canDownload === true,
  }
}

/**
 * 取文档预览二进制流（`GET /sources/documents/{id}/preview`）。
 *
 * 后端返回的是**裸流**，故用 `responseType: 'blob'` 并自行从 header 取 content-type。
 * 调用方负责 `URL.revokeObjectURL` 回收。
 */
export async function fetchKnowledgePreview(
  documentId: string,
): Promise<{ blob: Blob; contentType: string }> {
  const response = await http.get(`/sources/documents/${encodeURIComponent(documentId)}/preview`, {
    responseType: 'blob',
  })
  return {
    blob: response.data as Blob,
    contentType: String(response.headers?.['content-type'] ?? ''),
  }
}
