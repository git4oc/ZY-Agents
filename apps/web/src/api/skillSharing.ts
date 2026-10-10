import { createApiClient } from './client'
import type { SkillType } from './skills'

const api = createApiClient({ baseURL: '/api', timeout: 120000 })

function dataOf<T>(res: { data?: any }): T {
  return res.data?.data ?? res.data
}

export interface SkillSharePreview {
  shareId: string
  name: string
  description: string
  type: SkillType
  version: string
  fileCount: number
  childCount: number
  expiresAt: string
  alreadyInstalled: boolean
  hasConflict: boolean
  existingSkillId: string
}

export interface SkillShareCreated extends Omit<SkillSharePreview, 'alreadyInstalled' | 'hasConflict' | 'existingSkillId'> {
  token: string
}

export interface SharedSkillInstallResult {
  id: string
  name: string
  type: SkillType
  version: string
  duplicate: boolean
  enabled: boolean
}

export interface SkillShareMember {
  userId: string
  displayName: string
  username: string
  email: string
}

export interface SkillShareMemberPage {
  items: SkillShareMember[]
  nextCursor: string
  hasMore: boolean
}

export interface DirectSkillShareResult {
  shareId: string
  recipientCount: number
  recipients: SkillShareMember[]
}

export interface ReceivedSkillShare extends SkillSharePreview {
  deliveryId: string
  sender: SkillShareMember
  sharedAt: string
}

export interface ReceivedSkillSharePage {
  items: ReceivedSkillShare[]
  nextCursor: string
  hasMore: boolean
  pendingCount: number
}

export interface SkillUpdateNotice {
  id: string
  skillId: string
  name: string
  currentVersion: string
  newVersion: string
  releaseNotes: string
  changes: Array<'name' | 'description' | 'scenario' | 'instructions' | 'configuration' | 'package_content'>
  createdAt: string
  localModified: boolean
}

export async function fetchSkillUpdates(): Promise<{ items: SkillUpdateNotice[]; pendingCount: number }> {
  const res = await api.get('/skill-updates')
  return dataOf(res)
}

export async function installSkillUpdate(notificationId: string, confirmReplace = false): Promise<SharedSkillInstallResult> {
  const res = await api.post(`/skill-updates/${notificationId}/install`, { confirmReplace })
  return dataOf(res)
}

export async function createSkillShare(skillId: string, expiresInDays: number): Promise<SkillShareCreated> {
  const res = await api.post(`/skills/${skillId}/shares`, { expiresInDays })
  return dataOf<SkillShareCreated>(res)
}

export async function searchSkillShareMembers(payload: {
  keyword?: string
  cursor?: string
  limit?: number
}): Promise<SkillShareMemberPage> {
  const res = await api.get('/skill-share-directory', {
    params: { keyword: payload.keyword || '', cursor: payload.cursor || '', limit: payload.limit || 20 },
  })
  return dataOf<SkillShareMemberPage>(res)
}

export async function shareSkillWithUsers(skillId: string, recipientUserIds: string[]): Promise<DirectSkillShareResult> {
  const res = await api.post(`/skills/${skillId}/share-with-users`, { recipientUserIds })
  return dataOf<DirectSkillShareResult>(res)
}

export async function fetchReceivedSkillShares(cursor = '', limit = 20): Promise<ReceivedSkillSharePage> {
  const res = await api.get('/skill-share-inbox', { params: { cursor, limit } })
  return dataOf<ReceivedSkillSharePage>(res)
}

export interface SkillInboxCounts { pendingCount: number; sharedCount: number; updateCount: number; feedbackCount: number }
export async function fetchReceivedSkillShareCounts(): Promise<SkillInboxCounts> {
  const res = await api.get('/skill-share-inbox/count')
  const data = dataOf<Partial<SkillInboxCounts>>(res)
  return {
    pendingCount: Math.max(0, Number(data.pendingCount) || 0),
    sharedCount: Math.max(0, Number(data.sharedCount) || 0),
    updateCount: Math.max(0, Number(data.updateCount) || 0),
    feedbackCount: Math.max(0, Number(data.feedbackCount) || 0),
  }
}

export async function acceptReceivedSkillShare(deliveryId: string, replaceExisting = false): Promise<SharedSkillInstallResult> {
  const res = await api.post(`/skill-share-inbox/${deliveryId}/accept`, { replaceExisting })
  return dataOf<SharedSkillInstallResult>(res)
}

export async function declineReceivedSkillShare(deliveryId: string): Promise<void> {
  await api.post(`/skill-share-inbox/${deliveryId}/decline`)
}

export async function previewSkillShare(token: string): Promise<SkillSharePreview> {
  const res = await api.get(`/skill-shares/${encodeURIComponent(token)}`)
  return dataOf<SkillSharePreview>(res)
}

export async function installSharedSkill(token: string, replaceExisting = false): Promise<SharedSkillInstallResult> {
  const res = await api.post(`/skill-shares/${encodeURIComponent(token)}/install`, { replaceExisting })
  return dataOf<SharedSkillInstallResult>(res)
}

export async function revokeSkillShare(skillId: string, shareId: string): Promise<void> {
  await api.delete(`/skills/${skillId}/shares/${shareId}`)
}
