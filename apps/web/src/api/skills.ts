// ZY-Agents 技能管理（阶段 2 任务 #3）：核心 CRUD 接真后端 `/api/admin/skills`
// （admin.py；经 vite proxy `/api` → `/api/v1` 归一化）。
// 安装 zip / 发布 / 工作流与脚本生成（LLM 能力）为后续任务，保持桩。

import { notifyAuthExpiredFromResponse } from './authExpiry'
import { authenticatedHeaders, authenticatedJsonHeaders } from './authHeaders'

export type SkillType = 'writing_style' | 'workflow' | 'ordinary' | 'expert_package'

export interface ExpertPackageChild {
  slug: string
  name: string
  description: string
  version: string
}

export interface SkillPackageInfo {
  slug: string
  version: string
  digest: string
  files: Array<{ path: string; size: number; sha256: string }>
  warnings: Array<{ code: string; message: string; tool?: string; packageVersion?: string; skillVersion?: string }>
  kind: 'ordinary' | 'expert_package'
  children: ExpertPackageChild[]
}

export interface SkillInstallResult {
  id: string
  name: string
  slug: string
  description: string
  version: string
  type: 'ordinary' | 'expert_package'
  enabled: boolean
  duplicate: boolean
  updated: boolean
  fileCount: number
  digest: string
  warnings: Array<{ code: string; message: string; tool?: string; packageVersion?: string; skillVersion?: string; childSlug?: string; declaredName?: string; skillName?: string }>
  childCount: number
  children: ExpertPackageChild[]
  compatibility: {
    status: 'compatible' | 'warning'
    declaredTools: string[]
    referencedTools: string[]
    warnings: Array<{ code: string; message: string; tool?: string; packageVersion?: string; skillVersion?: string }>
  }
  upgrade?: SkillUpgradeInfo
}

export interface SkillUpgradeInfo {
  action: 'install' | 'duplicate' | 'upgrade' | 'replace' | 'downgrade'
  currentVersion?: string
  incomingVersion?: string
  currentName?: string
}

export interface SkillLifecycle {
  authoringMode: 'platform' | 'package'
  publicationStatus: 'draft' | 'published' | 'package'
  publishedVersion: string
  publishedReleaseId: string
  draftRevision: number
  hasUnpublishedChanges: boolean
}

export interface SkillFeedbackChannel {
  id: string
  role: 'owner' | 'member'
  commentCount: number
  unreadCount: number
  latestCommentAt: string
}

export interface SkillFeedbackSummary {
  commentCount: number
  unreadCount: number
  channels: SkillFeedbackChannel[]
}

export interface SkillItem {
  id: string
  mainId: string
  name: string
  description: string
  scenario: string
  type: SkillType
  config: Record<string, any>
  enabled: boolean
  createdAt: string
  updatedAt: string
  packageSource?: {
    kind?: string
    mode?: string
    fileName?: string
    coordinate?: string
    sender?: { userId?: string; displayName?: string; username?: string }
    distributionId?: string
    releaseId?: string
  }
  distributionId?: string
  feedback?: SkillFeedbackSummary
  locallyModified?: boolean
  lifecycle?: SkillLifecycle
  package?: SkillPackageInfo | null
}

export interface SkillPayload {
  name: string
  description: string
  scenario: string
  type: SkillType
  config: Record<string, any>
  enabled: boolean
}

export interface SelectableSkillItem {
  id: string
  mainId: string
  name: string
  description: string
  scenario: string
  type: SkillType
  sourceScope: 'user' | 'organization'
  enabled: boolean
  updatedAt?: string
}

export interface SelectableSkillPage {
  items: SelectableSkillItem[]
  nextCursor: string
  hasMore: boolean
}

export interface WorkflowStepDraft {
  id?: string
  text: string
}

export type WorkflowNodeType =
  | 'read_material'
  | 'extract_resources'
  | 'understand_image'
  | 'extract_info'
  | 'compute_metric'
  | 'data_collect'
  | 'browser_automation'
  | 'internal_search'
  | 'external_search'
  | 'call_tool'
  | 'script_plugin'
  | 'generate_content'
  | 'translate_rewrite'
  | 'fill_table'
  | 'review_check'
  | 'export_delivery'

export interface WorkflowNodeDraft {
  id?: string
  type: WorkflowNodeType
  title: string
  description: string
  businessConfig?: Record<string, any>
  boundWritingSkillId?: string
  outputAlias?: string
}

export interface WorkflowCheckResult {
  pass: boolean
  logic: {
    label: string
    level: 'success' | 'warning'
    summary: string
  }
  coverage: Array<{ key: string; label: string; covered: boolean }>
  readiness: Array<{ key: string; label: string; ready: boolean }>
  suggestions: string[]
  conclusion: string
  source?: string
  message?: string
}

export interface ScriptPluginCheckIssue {
  level: 'error' | 'warning'
  code: string
  message: string
  line?: number | null
}

export interface ScriptPluginCheckResult {
  pass: boolean
  rulePass: boolean
  errors: ScriptPluginCheckIssue[]
  warnings: ScriptPluginCheckIssue[]
  llm?: {
    pass: boolean
    level: 'success' | 'warning'
    summary: string
    risks: string[]
    suggestions: string[]
  } | null
  source?: string
  message?: string
}

export interface WritingStyleDraft {
  publishChannel: string[]
  contentForm: string[]
  targetAudience: string[]
  preferredStyle: string[]
  targetLength?: {
    min?: number
    max?: number
    unit?: string
  }
  sectionStructure?: Array<Record<string, any>>
  requiredSections: string[]
  requiredElements: string[]
  forbiddenElements: string[]
  inputProfile?: Record<string, any>
  contractJson?: Record<string, any>
  skillMarkdown?: string
}

export interface SkillSourceUpload {
  object_path: string
  filename?: string
  [key: string]: unknown
}

export interface SkillDraftEnrichment {
  contract_json?: Record<string, any>
  skill_markdown?: string
}

export interface TemplateAnalysis {
  skill_markdown?: string
  resources?: Record<string, any>
}

const EMPTY_SKILL: SkillItem = {
  id: '',
  mainId: '',
  name: '',
  description: '',
  scenario: '',
  type: 'ordinary',
  config: {},
  enabled: false,
  createdAt: '',
  updatedAt: '',
}

async function requestJson<T>(url: string, options: RequestInit = {}): Promise<T> {
  // 默认携带登录态（Authorization: Bearer）；调用方显式传的 headers 优先。
  // 修复：此前读接口（fetchSkills/fetchSelectableSkills/fetchSkill）不带认证头，
  // 登录后立刻进入"我的技能"会因 401 触发强制重新登录。
  const headers = { ...authenticatedHeaders(), ...((options.headers as Record<string, string>) || {}) }
  const resp = await fetch(url, { ...options, headers })
  notifyAuthExpiredFromResponse(resp, true)
  if (!resp.ok) {
    // 错误形状对齐页面读取契约：error.response.data.detail / error.response.data.error
    const body = await resp.json().catch(() => null)
    throw Object.assign(new Error(`Request failed: ${resp.status}`), {
      response: { status: resp.status, data: body },
    })
  }
  return resp.json() as Promise<T>
}

function mapSkill(row: any): SkillItem {
  return {
    id: String(row.id || ''),
    mainId: String(row.mainId || 'default'),
    name: String(row.name || ''),
    description: String(row.description || ''),
    scenario: String(row.scenario || ''),
    type: (row.type || 'ordinary') as SkillType,
    config: row.config || {},
    enabled: Boolean(row.enabled),
    createdAt: String(row.createdAt || ''),
    updatedAt: String(row.updatedAt || ''),
  }
}

export async function fetchSkills(_userId: string, _mainId = 'default'): Promise<SkillItem[]> {
  const data = await requestJson<{ items: any[] }>('/api/admin/skills')
  return (data.items || []).map(mapSkill)
}

export async function listSkills(_userId: string, _mainId = 'default'): Promise<SkillItem[]> {
  return fetchSkills(_userId, _mainId)
}

export async function fetchSelectableSkills(_payload: {
  userId: string
  mainId?: string
  scope?: 'all' | 'user' | 'organization'
  keyword?: string
  cursor?: string
  limit?: number
}): Promise<SelectableSkillPage> {
  const data = await requestJson<{ items: any[] }>('/api/admin/skills')
  const items = (data.items || []).map(mapSkill)
  return {
    items: items.map(item => ({
      id: item.id, mainId: item.mainId, name: item.name, description: item.description,
      scenario: item.scenario, type: item.type, sourceScope: 'organization' as const, enabled: item.enabled,
      updatedAt: item.updatedAt,
    })),
    nextCursor: '',
    hasMore: false,
  }
}

export async function fetchSkill(id: string, _userId: string, _mainId = 'default'): Promise<SkillItem> {
  return mapSkill(await requestJson<any>(`/api/admin/skills/${encodeURIComponent(id)}`))
}

export async function createSkill(_userId: string, _mainId: string, payload: SkillPayload): Promise<SkillItem> {
  const key = payload.name.trim() || `skill-${Date.now()}`
  const created = await requestJson<{ id: string }>('/api/admin/skills', {
    method: 'POST',
    headers: authenticatedJsonHeaders(),
    body: JSON.stringify({
      key, name: payload.name, description: payload.description, scenario: payload.scenario,
      type: payload.type, config: payload.config, enabled: payload.enabled,
    }),
  })
  return {
    id: created.id, mainId: 'default', name: payload.name, description: payload.description,
    scenario: payload.scenario, type: payload.type, config: payload.config,
    enabled: payload.enabled, createdAt: '', updatedAt: '',
  }
}

export async function updateSkill(id: string, userIdOrPayload: string | any, _mainId = 'default', payload?: SkillPayload): Promise<SkillItem> {
  const body: Record<string, any> = typeof userIdOrPayload === 'string' ? (payload || {}) : userIdOrPayload
  const updated = await requestJson<any>(`/api/admin/skills/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    headers: authenticatedJsonHeaders(),
    body: JSON.stringify({
      name: body.name, description: body.description, scenario: body.scenario,
      type: body.type, config: body.config, enabled: body.enabled,
    }),
  })
  return mapSkill(updated)
}

export async function deleteSkill(id: string, _userId: string, _mainId = 'default'): Promise<{ id: string }> {
  return requestJson<{ id: string }>(`/api/admin/skills/${encodeURIComponent(id)}`, {
    method: 'DELETE',
    headers: authenticatedHeaders(),
  })
}

export async function setSkillEnabled(_id: string, _userId: string, _mainId: string, enabled: boolean): Promise<SkillItem> {
  return mapSkill(await requestJson<any>(`/api/admin/skills/${encodeURIComponent(_id)}`, {
    method: 'PATCH',
    headers: authenticatedJsonHeaders(),
    body: JSON.stringify({ enabled }),
  }))
}

export async function installPersonalSkillZip(file: File, confirmReplace = false): Promise<SkillInstallResult> {
  // ZIP 安装接真：multipart 上传到 `/api/admin/skills/install`（admin.py，skill.manage 权限）。
  // 错误形状对齐 skillInstallError.ts 契约：error.response.data.detail = {code, file, field}。
  const form = new FormData()
  form.append('file', file)
  form.append('confirm_replace', String(confirmReplace))
  const resp = await fetch('/api/admin/skills/install', {
    method: 'POST',
    headers: authenticatedHeaders(),
    body: form,
  })
  notifyAuthExpiredFromResponse(resp, true)
  const body = await resp.json().catch(() => null)
  if (!resp.ok) {
    throw Object.assign(new Error(`Skill install failed: ${resp.status}`), {
      response: { status: resp.status, data: body },
    })
  }
  return body as SkillInstallResult
}

export async function publishSkill(_id: string, _version = '', _releaseNotes = ''): Promise<{ skill: SkillItem; release: { id: string; version: string; digest: string; releaseNotes: string; createdAt: string } }> {
  throw new Error('skills are not supported')
}

export async function fetchSkillReleases(_id: string): Promise<Array<{ id: string; version: string; digest: string; releaseNotes: string; createdAt: string }>> {
  return []
}

export async function generateWorkflowSteps(_payload: {
  name: string
  description?: string
  scenario?: string
  existingSteps?: string[]
  maxSteps?: number
  mode?: 'generate' | 'optimize' | 'supplement' | 'supplement_step'
  supplement?: string
  nodeCatalog?: Array<{ type: WorkflowNodeType; name: string; usageDescription: string; usageExample: string }>
}): Promise<{ steps: WorkflowStepDraft[]; source?: string; message?: string }> {
  return { steps: [], message: 'skills are not supported' }
}

export async function generateWorkflowNodes(_payload: {
  name: string
  description?: string
  scenario?: string
  existingNodes?: WorkflowNodeDraft[]
  maxNodes?: number
  mode?: 'generate' | 'optimize' | 'supplement' | 'supplement_step'
  supplement?: string
  nodeCatalog?: Array<{ type: WorkflowNodeType; name: string; usageDescription: string; usageExample: string }>
}): Promise<{ nodes: WorkflowNodeDraft[]; source?: string; message?: string }> {
  return { nodes: [], message: 'skills are not supported' }
}

export async function checkWorkflowNodes(_payload: {
  name: string
  description?: string
  scenario?: string
  nodes: WorkflowNodeDraft[]
  steps?: string[]
}): Promise<WorkflowCheckResult> {
  return {
    pass: false,
    logic: { label: '', level: 'warning', summary: 'skills are not supported' },
    coverage: [],
    readiness: [],
    suggestions: [],
    conclusion: 'skills are not supported',
  }
}

export async function checkWorkflowLogic(_payload: {
  name: string
  description?: string
  scenario?: string
  steps: string[]
  nodes?: WorkflowNodeDraft[]
}): Promise<WorkflowCheckResult> {
  return {
    pass: false,
    logic: { label: '', level: 'warning', summary: 'skills are not supported' },
    coverage: [],
    readiness: [],
    suggestions: [],
    conclusion: 'skills are not supported',
  }
}

export async function checkScriptPlugin(_payload: {
  code: string
  nodeTitle?: string
  nodeDescription?: string
}): Promise<ScriptPluginCheckResult> {
  return {
    pass: false,
    rulePass: false,
    errors: [{ level: 'error', code: 'unsupported', message: 'skills are not supported' }],
    warnings: [],
  }
}

export async function fixScriptPlugin(_payload: {
  code: string
  nodeTitle?: string
  nodeDescription?: string
  issues?: Array<Record<string, any>>
}): Promise<{ code: string; notes: string[]; check?: ScriptPluginCheckResult }> {
  throw new Error('skills are not supported')
}

export async function generateScriptPlugin(_payload: {
  processingInstruction: string
  nodeTitle?: string
  nodeDescription?: string
  skillName?: string
  skillDescription?: string
  scenario?: string
  selectedInputSource?: string
  selectedInputTypes?: string[]
  workflowNodes?: WorkflowNodeDraft[]
}): Promise<{ code: string; notes: string[]; check?: ScriptPluginCheckResult }> {
  throw new Error('skills are not supported')
}

export async function enrichWritingStyleDraft(_payload: {
  userId: string
  mainId: string
  name: string
  description?: string
  scenario?: string
  draft: WritingStyleDraft
}): Promise<{
  inputProfile: Record<string, any>
  contractJson: Record<string, any>
  skillMarkdown: string
}> {
  return { inputProfile: {}, contractJson: {}, skillMarkdown: '' }
}

export async function uploadSkillSource(_userId: string, _file: File): Promise<SkillSourceUpload> {
  throw new Error('skills are not supported')
}

export async function analyzeTemplate(_file: File): Promise<TemplateAnalysis> {
  return {}
}

export async function generateSkill(_payload: any): Promise<SkillItem> {
  return EMPTY_SKILL
}

export async function enrichSkillDraft(_payload: any): Promise<SkillDraftEnrichment> {
  return {}
}
