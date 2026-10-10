// 用户侧模型目录：GET /api/v1/models（后端按 org/user 读 Profile 编译输入里的
// 可见模型，不含 api_key）。模型选择器据此渲染；对话仍由后端按 Profile 自选模型。

import { authenticatedJsonHeaders } from './authHeaders'

export interface ChatModelOption {
  id: string
  displayName: string
  modelName: string
  providerName: string
  providerType: string
  runtimeKind?: string
  capabilities?: string[]
  isDefault: boolean
  healthStatus: string
}

function mapModel(item: any): ChatModelOption {
  return {
    id: String(item?.id ?? ''),
    displayName: String(item?.displayName ?? ''),
    modelName: String(item?.modelName ?? ''),
    providerName: String(item?.providerName ?? ''),
    providerType: String(item?.providerType ?? ''),
    runtimeKind: item?.runtimeKind,
    capabilities: Array.isArray(item?.capabilities) ? item.capabilities : [],
    isDefault: Boolean(item?.isDefault),
    healthStatus: String(item?.healthStatus ?? 'active'),
  }
}

export async function fetchChatModels(_mainId = 'default'): Promise<ChatModelOption[]> {
  const resp = await fetch('/api/v1/models', { headers: authenticatedJsonHeaders() })
  if (!resp.ok) {
    throw new Error(`models ${resp.status}`)
  }
  const data = await resp.json()
  const items = Array.isArray(data?.items) ? data.items : []
  return items.map(mapModel)
}

// 图像模型目录：阶段 1 后端未提供端点，保持空态。
export async function fetchImageModels(_mainId = 'default'): Promise<ChatModelOption[]> {
  return []
}
