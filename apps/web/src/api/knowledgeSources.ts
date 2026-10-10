import { notifyAuthExpiredFromResponse } from './authExpiry'

export interface KnowledgeSourceDocument {
  id: string
  scope: string
  name: string
  originalFilename: string
  fileExt: string
  mimeType: string
  previewMimeType: string
  previewStatus: string
  chunkCount: number
  canDownload?: boolean
}

export interface KnowledgeSourceChunk {
  id: string
  documentId: string
  chunkId: string
  chunkStage: string
  ordinal: number
  text: string
  contextualText: string
  titlePath: string[]
  pageNo: number | string | null
  contentType: string
  sourceChunkIds: string[]
  metadata: Record<string, any>
}

export function knowledgeSourceAuthHeaders(token?: string | null): Record<string, string> {
  const resolved = token ?? (typeof window !== 'undefined' ? window.localStorage.getItem('auth_token') : '')
  return resolved ? { Authorization: `Bearer ${resolved}` } : {}
}

async function readJson<T>(url: string, token?: string | null): Promise<T> {
  const resp = await fetch(url, { headers: knowledgeSourceAuthHeaders(token) })
  notifyAuthExpiredFromResponse(resp, Boolean(token))
  if (!resp.ok) throw new Error(`Request failed: ${resp.status}`)
  return resp.json() as Promise<T>
}

export function knowledgeSourcePreviewUrl(documentId: string): string {
  return `/api/knowledge/sources/documents/${encodeURIComponent(documentId)}/preview`
}

export async function fetchKnowledgeSourceDocument(documentId: string, token?: string | null) {
  return readJson<KnowledgeSourceDocument>(
    `/api/knowledge/sources/documents/${encodeURIComponent(documentId)}`,
    token,
  )
}

export async function fetchKnowledgeSourceChunk(documentId: string, chunkId: string, token?: string | null) {
  return readJson<KnowledgeSourceChunk>(
    `/api/knowledge/sources/documents/${encodeURIComponent(documentId)}/chunks/${encodeURIComponent(chunkId)}`,
    token,
  )
}

export async function fetchKnowledgeSourcePreview(documentId: string, token?: string | null) {
  const resp = await fetch(knowledgeSourcePreviewUrl(documentId), { headers: knowledgeSourceAuthHeaders(token) })
  notifyAuthExpiredFromResponse(resp, Boolean(token))
  if (!resp.ok) throw new Error(`Request failed: ${resp.status}`)
  return resp.blob()
}
