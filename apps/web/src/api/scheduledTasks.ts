import axios from 'axios'
import { installAuthExpiryInterceptor } from './authExpiry'

// ZY-Agents 主服务（/api/v1）。阶段 1 后端**没有**定时任务端点：请求会真实
// 发出并收到 404/501，这里翻译成可识别的业务错误透出给 UI，绝不假装成功。
const client = axios.create({ baseURL: '/api/v1', timeout: 20000 })
installAuthExpiryInterceptor(client)

export type ScheduleKind = 'once' | 'daily' | 'weekly'
export type ScheduledSessionMode = 'fixed' | 'new_per_run'

export type ScheduledJob = {
  id: string
  name: string
  prompt: string
  schedule_kind: ScheduleKind
  timezone: string
  run_at: string
  weekdays: number[]
  session_mode: ScheduledSessionMode
  session_id?: string | null
  session_title_template: string
  enabled: boolean
  output_spec: Record<string, any>
  next_run_at?: string | null
  last_run_at?: string | null
  last_run_status?: string
  last_session_id?: string | null
  created_at: string
  updated_at: string
}

export type ScheduledJobDraft = Pick<
  ScheduledJob,
  'name' | 'prompt' | 'schedule_kind' | 'timezone' | 'run_at' | 'weekdays' |
  'session_mode' | 'session_id' | 'session_title_template' | 'enabled' | 'output_spec'
>

/** 端点不存在（阶段 1 未开放）时的统一业务错误文案。 */
export const SCHEDULED_JOBS_UNAVAILABLE = '定时任务功能尚未开放'

function config(token: string) {
  return { headers: { Authorization: `Bearer ${token}` } }
}

function errorMessageOf(error: any, fallback: string): string {
  return error?.response?.data?.error?.message
    || error?.response?.data?.detail?.message
    || (typeof error?.response?.data?.detail === 'string' ? error.response.data.detail : '')
    || error?.message
    || fallback
}

/** 404/405/501 → "功能尚未开放"；其余错误原样透出（不吞错、不假装成功）。 */
function rethrow(error: any, fallback: string): never {
  const status = Number(error?.response?.status) || 0
  if (status === 404 || status === 405 || status === 501) {
    throw new Error(SCHEDULED_JOBS_UNAVAILABLE)
  }
  throw new Error(errorMessageOf(error, fallback))
}

export async function listScheduledJobs(token: string): Promise<ScheduledJob[]> {
  try {
    const response = await client.get('/scheduled-jobs', config(token))
    const data = response.data
    const items = Array.isArray(data) ? data : (Array.isArray(data?.items) ? data.items : [])
    return items
  } catch (error) {
    rethrow(error, '加载定时任务失败')
  }
}

export async function createScheduledJob(token: string, draft: ScheduledJobDraft): Promise<ScheduledJob> {
  try {
    const response = await client.post('/scheduled-jobs', draft, config(token))
    const data = response.data
    return (data?.item || data) as ScheduledJob
  } catch (error) {
    rethrow(error, '创建定时任务失败')
  }
}

export async function updateScheduledJob(
  token: string,
  id: string,
  patch: Partial<ScheduledJobDraft>,
): Promise<ScheduledJob> {
  try {
    const response = await client.patch(`/scheduled-jobs/${encodeURIComponent(id)}`, patch, config(token))
    const data = response.data
    return (data?.item || data) as ScheduledJob
  } catch (error) {
    rethrow(error, '更新定时任务失败')
  }
}

export async function deleteScheduledJob(token: string, id: string): Promise<void> {
  try {
    await client.delete(`/scheduled-jobs/${encodeURIComponent(id)}`, config(token))
  } catch (error) {
    rethrow(error, '删除定时任务失败')
  }
}

export async function runScheduledJobNow(token: string, id: string): Promise<void> {
  try {
    await client.post(`/scheduled-jobs/${encodeURIComponent(id)}/run-now`, {}, config(token))
  } catch (error) {
    rethrow(error, '启动定时任务失败')
  }
}
