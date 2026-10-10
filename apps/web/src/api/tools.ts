// ZY-Agents 阶段 1 无 external-tools 端点（admin 端点本任务不接）：
// stub 为空数据（保持签名），工具页面自然空态。

export type ToolType = 'http' | 'mcp'
export type ToolStatus = 'active' | 'disabled'
export type TestStatus = 'untested' | 'passed' | 'failed'

export interface ExternalToolItem {
  id: string
  mainId: string
  scope?: 'user' | 'organization'
  ownerUserId?: string
  name: string
  type: ToolType
  description: string
  usageHint: string
  tags: string[]
  status: ToolStatus
  config: Record<string, any>
  lastTestStatus: TestStatus
  lastTestAt: string
  lastTestMessage: string
  discoveredTools: Array<{ name: string; description: string; inputSchema?: Record<string, any> }>
  createdAt: string
  updatedAt: string
}

export interface ToolPayload {
  name: string
  type: ToolType
  description: string
  usageHint: string
  tags: string[]
  status: ToolStatus
  config: Record<string, any>
}

export async function fetchTools(_userId: string, _mainId = 'default'): Promise<ExternalToolItem[]> {
  return []
}

export async function fetchTool(_id: string, _userId: string, _mainId = 'default'): Promise<ExternalToolItem> {
  throw new Error('tools are not supported')
}

export async function createTool(_userId: string, _mainId: string, _payload: ToolPayload): Promise<ExternalToolItem> {
  throw new Error('tools are not supported')
}

export async function updateTool(_id: string, _userId: string, _mainId: string, _payload: ToolPayload): Promise<ExternalToolItem> {
  throw new Error('tools are not supported')
}

export async function patchTool(_id: string, _userId: string, _mainId: string, _payload: Partial<ToolPayload>): Promise<ExternalToolItem> {
  throw new Error('tools are not supported')
}

export async function deleteTool(_id: string, _userId: string, _mainId: string): Promise<{ id: string }> {
  throw new Error('tools are not supported')
}

export async function testTool(_id: string, _userId: string, _mainId: string, _input: Record<string, any>): Promise<Record<string, any>> {
  throw new Error('tools are not supported')
}

export async function testDraftTool(_userId: string, _mainId: string, _payload: ToolPayload, _input: Record<string, any>): Promise<Record<string, any>> {
  throw new Error('tools are not supported')
}

export async function discoverMcpTools(_id: string, _userId: string, _mainId: string): Promise<{ success: boolean; message: string; tools: ExternalToolItem['discoveredTools'] }> {
  return { success: false, message: 'tools are not supported', tools: [] }
}

export async function generateToolDescription(_payload: {
  name: string
  type: ToolType
  existingDescription?: string
}): Promise<{ description: string }> {
  return { description: '' }
}
