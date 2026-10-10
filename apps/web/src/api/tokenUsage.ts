// ZY-Agents 阶段 1 无 token-usage 端点：stub 为空数据（保持签名），
// 用量页面自然空态。

export type TokenUsageSummary = {
  total_calls: number
  internal_calls: number
  total_tokens: number
  prompt_tokens: number
  completion_tokens: number
  avg_tokens: number
  last_called_at?: string | null
  last_24h_calls: number
  last_24h_internal_calls: number
  last_24h_tokens: number
  top_models: Array<{ model_name: string; calls: number; tokens: number }>
}

export type TokenUsageItem = {
  request_id: string
  user_request_id?: string
  main_id: string
  user_id: string
  session_id: string
  trace_id: string
  stage: string
  intent: string
  node_id: string
  status: string
  model_name: string
  model_names?: string[]
  model_id: string
  prompt: string
  request_title_zh: string
  request_title_en: string
  start_time: number
  end_time: number
  total_tokens: number
  prompt_tokens: number
  completion_tokens: number
  calls?: number
  push_status: string
  push_error: string
  created_at?: string | null
  updated_at?: string | null
}

export type TokenUsagePage = {
  summary: TokenUsageSummary
  items: TokenUsageItem[]
  offset: number
  limit: number
  total: number
  has_more: boolean
}

const EMPTY_SUMMARY: TokenUsageSummary = {
  total_calls: 0,
  internal_calls: 0,
  total_tokens: 0,
  prompt_tokens: 0,
  completion_tokens: 0,
  avg_tokens: 0,
  last_called_at: null,
  last_24h_calls: 0,
  last_24h_internal_calls: 0,
  last_24h_tokens: 0,
  top_models: [],
}

export async function listTokenUsage(
  _userId: string,
  options: {
    mainId?: string
    limit?: number
    offset?: number
    q?: string
    stage?: string
    status?: string
    token?: string
  } = {}
): Promise<TokenUsagePage> {
  const limit = options.limit ?? 12
  return {
    summary: EMPTY_SUMMARY,
    items: [],
    offset: options.offset ?? 0,
    limit,
    total: 0,
    has_more: false,
  }
}
