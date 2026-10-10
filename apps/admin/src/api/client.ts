import axios, { type AxiosInstance } from 'axios'

/** 管理端 token 的 localStorage key（与 user-web 的 auth_token 相互独立）。 */
export const ADMIN_TOKEN_KEY = 'zy_admin_token'

export function getToken(): string {
  if (typeof window === 'undefined') return ''
  return window.localStorage.getItem(ADMIN_TOKEN_KEY) || ''
}

export function setToken(token: string): void {
  window.localStorage.setItem(ADMIN_TOKEN_KEY, token)
}

export function clearToken(): void {
  window.localStorage.removeItem(ADMIN_TOKEN_KEY)
}

/** 登录态失效回调（由 main 注册，避免与 router 循环依赖）。 */
let onUnauthorized: (() => void) | null = null

export function setUnauthorizedHandler(handler: () => void): void {
  onUnauthorized = handler
}

// 后端路由自带 /api/v1 前缀，代理 /api 原样透传。
export const http: AxiosInstance = axios.create({
  baseURL: '/api/v1',
  timeout: 30000,
})

http.interceptors.request.use((config) => {
  const token = getToken()
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

http.interceptors.response.use(
  (response) => response,
  (error) => {
    if (Number(error?.response?.status) === 401) {
      clearToken()
      onUnauthorized?.()
    }
    return Promise.reject(error)
  },
)

/** 后端统一错误体：{error: {code, message, details, trace_id}}。 */
export function errorMessage(error: unknown, fallback = '请求失败'): string {
  const raw = error as {
    response?: { data?: { error?: { message?: string }; detail?: { message?: string } | string } }
    message?: string
  }
  const detail = raw?.response?.data?.detail
  return (
    raw?.response?.data?.error?.message
    || (typeof detail === 'string' ? detail : detail?.message)
    || raw?.message
    || fallback
  )
}

export function errorStatus(error: unknown): number {
  return Number((error as { response?: { status?: number } })?.response?.status) || 0
}

/** 403 = 缺少管理角色码（后端 _Permission.guard / branding._require）。 */
export function isPermissionDenied(error: unknown): boolean {
  return errorStatus(error) === 403
}
