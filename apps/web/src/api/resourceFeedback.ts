import { createApiClient } from './client'

const api = createApiClient({ baseURL: '/api', timeout: 30000 })
const dataOf = <T>(res: { data?: any }): T => res.data?.data ?? res.data

export interface FeedbackComment {
  id: string
  content: string
  parentId: string
  rootId: string
  replyTo: { userId?: string; displayName?: string }
  author: { userId: string; displayName: string }
  likes: number
  likedByMe: boolean
  mine: boolean
  createdAt: string
}

export interface FeedbackThread {
  items: FeedbackComment[]
  commentCount: number
  likes: number
  likedByMe: boolean
  hasMore: boolean
  nextCursor: string
  focus?: { kind: string; commentId: string } | null
}

export async function fetchFeedback(type: string, id: string, cursor = '', limit = 30): Promise<FeedbackThread> {
  return dataOf(await api.get(`/resource-feedback/${encodeURIComponent(type)}/${encodeURIComponent(id)}`, { params: { cursor, limit } }))
}
export async function addFeedbackComment(type: string, id: string, content: string, parentId = ''): Promise<FeedbackComment> {
  return dataOf(await api.post(`/resource-feedback/${encodeURIComponent(type)}/${encodeURIComponent(id)}/comments`, { content, parentId }))
}
export async function deleteFeedbackComment(commentId: string): Promise<void> {
  await api.delete(`/resource-feedback/comments/${encodeURIComponent(commentId)}`)
}
export async function toggleFeedbackLike(type: string, id: string): Promise<{ likes: number; likedByMe: boolean }> {
  return dataOf(await api.post(`/resource-feedback/${encodeURIComponent(type)}/${encodeURIComponent(id)}/like`))
}
export async function toggleFeedbackCommentLike(commentId: string): Promise<{ likes: number; likedByMe: boolean }> {
  return dataOf(await api.post(`/resource-feedback/comments/${encodeURIComponent(commentId)}/like`))
}
export async function fetchFeedbackNotifications(): Promise<{ items: Array<{ id: string; resourceType: string; resourceId: string; name: string; actor: { displayName?: string }; createdAt: string }>; unreadCount: number }> {
  return dataOf(await api.get('/resource-feedback-notifications'))
}
