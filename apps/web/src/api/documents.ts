// ZY-Agents 阶段 1 无 documents 渲染端点：stub（保持签名）。
// artifact 渲染在无 object_path 的会话里本就不会触发，调用时返回空 URL。

export type RenderDocumentRequest = {
  user_id: string
  content: string
  format: 'pdf' | 'docx' | 'ppt' | 'pptx' | 'md' | 'markdown'
  filename?: string
  title?: string
}

export type RenderDocumentResponse = {
  type: 'pdf' | 'docx' | 'ppt' | 'pptx' | 'md' | 'markdown'
  url: string
  filename?: string
  title?: string
  object_path?: string
}

export async function renderDocument(payload: RenderDocumentRequest): Promise<RenderDocumentResponse> {
  return {
    type: payload.format === 'markdown' ? 'md' : payload.format,
    url: '',
    filename: payload.filename,
    title: payload.title,
  }
}

export type RenderPresentationPptxRequest = {
  user_id: string
  blueprint_object_path: string
  filename?: string
  title?: string
}

export async function renderPresentationPptx(payload: RenderPresentationPptxRequest): Promise<RenderDocumentResponse> {
  return {
    type: 'pptx',
    url: '',
    filename: payload.filename,
    title: payload.title,
  }
}

export async function fetchBlueprintJson(_userId: string, _objectPath: string): Promise<any> {
  return {}
}

export async function saveBlueprint(payload: {
  user_id: string
  blueprint_object_path: string
  blueprint: any
}): Promise<{ object_path: string; url: string }> {
  return { object_path: payload.blueprint_object_path, url: '' }
}

export async function signDocument(payload: { user_id: string; object_path: string; expires?: number }): Promise<{ url: string }> {
  return { url: payload.object_path || '' }
}
