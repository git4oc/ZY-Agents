import { http } from './client'

export type AdminProfile = {
  userId: string
  username: string
  displayName: string
  orgUnitId: string
  roles: string[]
  permissions: string[]
}

/** POST /auth/login → **裸 dict** {access_token, token_type, expires_in}（无 {code,data} 包裹）。 */
export async function loginRequest(username: string, password: string): Promise<string> {
  const response = await http.post('/auth/login', { username, password })
  const token = (response.data as { access_token?: string } | null)?.access_token
  if (typeof token !== 'string' || !token) {
    throw new Error('登录响应缺少 access_token')
  }
  return token
}

/** GET /auth/me → {user: {id, username, display_name}, org_unit: {id}, roles[], permissions[]}。 */
export async function fetchMe(): Promise<AdminProfile> {
  const response = await http.get('/auth/me')
  const data = (response.data || {}) as {
    user?: { id?: string; username?: string; display_name?: string }
    org_unit?: { id?: string }
    roles?: unknown
    permissions?: unknown
  }
  return {
    userId: String(data.user?.id ?? ''),
    username: String(data.user?.username ?? ''),
    displayName: String(data.user?.display_name ?? data.user?.username ?? ''),
    orgUnitId: String(data.org_unit?.id ?? ''),
    roles: Array.isArray(data.roles) ? data.roles.map(String) : [],
    permissions: Array.isArray(data.permissions) ? data.permissions.map(String) : [],
  }
}
