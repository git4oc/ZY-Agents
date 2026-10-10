import axios from 'axios'
import { t } from '../hooks/i18n'
import { installAuthExpiryInterceptor } from './authExpiry'

// 主服务接口前缀为 /api/v1；开发期由 vite 代理的 /api 规则转发到后端。
// 契约：成功响应为裸 dict（无 {code:0,data} 包裹）；非 2xx 错误体为
// {error: {code, message, details, trace_id}}。
const client = axios.create({
  baseURL: '/api/v1',
  timeout: 15000,
})
installAuthExpiryInterceptor(client)

function errorMessageOf(error: any, fallback: string): string {
  return error?.response?.data?.error?.message
    || error?.response?.data?.detail?.message
    || error?.response?.data?.detail
    || error?.message
    || fallback
}

export type AuthResult = {
  ok: boolean
  token?: string
  profile?: UserProfile
  message?: string
  requiresTenantSelection?: boolean
  challengeToken?: string
  tenantCandidates?: TenantCandidate[]
}

export type TenantCandidate = {
  mainId: string
  orgName: string
  spaceType?: 'personal' | 'enterprise'
  userId: string
  displayName: string
  username: string
  canAccessAdmin?: boolean
  edition?: 'community' | 'cloud'
  billingEnabled?: boolean
  memberLimit?: number | null
}

export type UserProfile = {
  userId?: number | string
  name?: string
  username?: string
  phone?: string
  email?: string
  avatar?: string
  mainId?: string
  orgName?: string
  spaceType?: 'personal' | 'enterprise'
  canAccessAdmin?: boolean
  tier?: 'community' | 'free' | 'plus' | 'pro' | 'enterprise'
  edition?: 'community' | 'cloud'
  billingEnabled?: boolean
  memberLimit?: number | null
  availableTenants?: TenantCandidate[]
  agentPolicy?: AgentPolicySnapshot
}

export type AgentCapabilityKey =
  | 'content_generation'
  | 'image_generation'
  | 'code_generation'
  | 'browser_automation'
  | 'internal_knowledge'

export type AgentPolicySnapshot = {
  capabilities: Record<AgentCapabilityKey, boolean>
  // 旧版的资源级聚合开关。企业部署现按单个资源判定 Skill 与工具权限，
  // 因此较新的快照可能不再包含这些字段。
  toolAccessMode?: 'all' | 'selected'
  toolIds?: string[]
  skillAccessMode?: 'all' | 'selected'
  skillIds?: string[]
  roleIds: string[]
  roleNames: string[]
  migrationPending?: boolean
  version?: string
}

/**
 * GET /auth/me → {user: {id, username, display_name}, org_unit: {id},
 * roles[], permissions[], agentPolicy} → UserProfile。
 * mainId 取 org_unit.id（runtime 依赖非 'default' 的 mainId 才放行发送）。
 *
 * agentPolicy 以 /auth/me 响应为唯一真相：capabilities 逐 key 用 `?? false`
 * 兜底（缺失即"无该能力"，不假装有能力），roleIds/roleNames 缺失回退 roles。
 */
function buildProfile(data: any): UserProfile {
  const roles: string[] = Array.isArray(data?.roles) ? data.roles.map(String) : []
  const mainId = String(data?.org_unit?.id || 'primary') || 'primary'
  const policy = data?.agentPolicy
  const capabilities = policy?.capabilities
  return {
    userId: data?.user?.id ?? data?.id ?? '',
    name: data?.user?.display_name || data?.user?.username || '',
    username: data?.user?.username || '',
    email: '',
    avatar: '',
    mainId,
    orgName: '',
    spaceType: 'personal',
    canAccessAdmin: roles.includes('admin'),
    edition: 'community',
    billingEnabled: false,
    memberLimit: null,
    availableTenants: [],
    agentPolicy: {
      capabilities: {
        content_generation: capabilities?.content_generation ?? false,
        image_generation: capabilities?.image_generation ?? false,
        code_generation: capabilities?.code_generation ?? false,
        browser_automation: capabilities?.browser_automation ?? false,
        internal_knowledge: capabilities?.internal_knowledge ?? false,
      },
      roleIds: Array.isArray(policy?.roleIds) ? policy.roleIds.map(String) : roles,
      roleNames: Array.isArray(policy?.roleNames) ? policy.roleNames.map(String) : roles,
    },
  }
}

/** POST /auth/login {username, password} → 裸 {access_token, token_type, expires_in}。 */
export async function loginWithPassword(
  username: string,
  password: string,
  mainId = ''
): Promise<AuthResult> {
  try {
    const response = await client.post('/auth/login', { username, password })
    const token = response.data?.access_token
    if (!token) {
      return { ok: false, message: response.data?.error?.message || t('api.auth.login_failed') }
    }
    const me = await fetchUserProfile(token)
    return { ok: true, token, profile: me.data }
  } catch (error: any) {
    return { ok: false, message: errorMessageOf(error, t('api.auth.login_failed')) }
  }
}

/** ZY-Agents 无多租户选择流程：占位失败，保持 UI 兼容。 */
export async function selectTenantAndLogin(_challengeToken: string, _mainId: string): Promise<AuthResult> {
  return { ok: false, message: t('api.auth.login_failed') }
}

/** ZY-Agents 无多租户切换流程：占位失败，保持 UI 兼容。 */
export async function switchTenant(_token: string, _mainId: string): Promise<AuthResult> {
  return { ok: false, message: t('api.auth.switch_org_failed') }
}

/** GET /auth/me → {user, org_unit, roles[], permissions[]}。 */
export async function fetchUserProfile(token: string): Promise<{ ok: boolean; data?: UserProfile; message?: string }> {
  try {
    const response = await client.get('/auth/me', {
      headers: {
        Authorization: `Bearer ${token}`,
      },
    })
    const data = response.data
    if (!data || typeof data !== 'object' || data.error) {
      return { ok: false, message: data?.error?.message || t('api.auth.get_user_info_failed') }
    }
    return { ok: true, data: buildProfile(data) }
  } catch (error: any) {
    return { ok: false, message: errorMessageOf(error, t('api.auth.get_user_info_failed')) }
  }
}

/** ZY-Agents 阶段 1 不支持资料修改：占位失败。 */
export async function updateUserProfile(
  _token: string,
  _name: string,
): Promise<{ ok: boolean; data?: UserProfile; message?: string }> {
  return { ok: false, message: t('api.auth.update_profile_failed') }
}

/** ZY-Agents 阶段 1 不支持头像上传：占位失败。 */
export async function uploadUserAvatar(
  _token: string,
  _file: File,
): Promise<{ ok: boolean; data?: UserProfile; message?: string }> {
  return { ok: false, message: t('api.auth.upload_avatar_failed') }
}

export async function logoutWithToken(token: string): Promise<void> {
  try {
    await client.post('/auth/logout', {}, {
      headers: {
        Authorization: `Bearer ${token}`,
      },
    })
  } catch {
    // ignore — 无状态 JWT：客户端丢弃 token 即可
  }
}

/**
 * ZY-Agents 阶段 1 无配额端点：明确返回"不可用"，绝不编造剩余额度。
 * 调用方（侧边栏配额区 / TokenUsagePage）据此展示"暂不可用"，
 * runtime 发送闸门在不可用时放行（无配额系统 ≠ 额度耗尽）。
 */
export async function fetchOrgBilling(_token: string): Promise<{ ok: boolean; data?: any; message?: string }> {
  return {
    ok: false,
    data: { available: false, reason: 'quota_not_supported' },
    message: '配额功能尚未开放',
  }
}

/** ZY-Agents 无管理端 SSO 流程：占位失败。 */
export async function getAdminSSOToken(_token: string): Promise<{ ok: boolean; ssoToken?: string; message?: string }> {
  return { ok: false, message: t('api.auth.auth_failed') }
}
