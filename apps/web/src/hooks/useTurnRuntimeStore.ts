import { computed, reactive, ref } from 'vue'
import { fetchOrgBilling } from '../api/auth'
import { uploadChatDocument, uploadChatImage, type UploadedDocument, type UploadedImage } from '../api/chat'
import { getSession, type ChatMessage, type SessionDetail, type SessionSummary } from '../api/sessions'
import { fetchChatMessageEvents, startTurnStream, TurnStreamHttpError, TURN_ALREADY_RUNNING, type TurnStreamHandle } from './useTurnStream'
import { getLocale, t } from './i18n'
import { resumeBrowserInterventionTaskUntilSettled } from './tasks/browserInterventionTaskFlow'
import {
  browserInterventionTransition,
  normalizeBrowserIntervention,
} from './browser/browserInterventionProjection'
import type { ExecutionStoreV3 } from '../modules/timeline-v3/stores/timelineStore'
import type { DshTaskChangeSet } from '../bridge/types'
import { isTimelineEventV3, type TimelineEventV3 } from '../modules/timeline-v3/domain/protocol'
import { ensureMessageExecutionV3 } from '../modules/timeline-v3/stores/messageTimeline'
import { refreshAfterRun } from './turnRuntimeRefresh'
import { applyAssistantContentEvent } from '../modules/timeline-v3/domain/assistantContent'
import type { BrowserAssistanceHandoff } from './browser/useBrowserWorkspace'
import { stopTurnGeneration } from './turnStopFlow'
import { foreignRunForViewer, type ForeignRun } from './runPresenceWatcher'

export type RuntimeDocumentInfo = {
  id?: string
  type: 'pdf' | 'docx' | 'ppt' | 'pptx' | 'md' | 'html' | 'xlsx' | 'presentation_preview_bundle'
  url: string
  filename?: string
  title?: string
  object_path?: string
  signed_url?: string
  content_type?: string
  size?: number
  bundle?: Record<string, any>
}

export type RuntimeImageInfo = {
  object_path?: string
  url?: string
  signed_url?: string
  filename?: string
  content_type?: string
  size?: number
}

export type RuntimeMessage = {
  role: 'user' | 'assistant'
  content: string
  plan?: any
  progress?: { content: string; timestamp?: string }[]
  _id?: string
  _execV3?: ExecutionStoreV3
  _provisionalTextByItem?: Record<string, string>
  _backendSid?: string
  message_id?: string
  /** Who wrote this message (todo 28): on optimistic pushes it is the viewer's
   *  own user_id; for messages fetched through session-GET it is the author's
   *  user_id kept by normalizeMessages. Missing on legacy/system-shaped
   *  messages. */
  user_id?: string
  execution_events?: any[]
  documents?: RuntimeDocumentInfo[]
  images?: RuntimeImageInfo[]
  evidence_bundles?: any[]
  proofPacks?: any[]
  trigger_source?: string
  scheduled_job_id?: string
  scheduled_run_id?: string
  created_at?: string
  _codeChanges?: DshTaskChangeSet
}

export type PendingRuntimeDocument = {
  file: File
  kind: RuntimeDocumentInfo['type']
}

export type RuntimeIntervention = {
  reason: string
  category: string
  url?: string
  domain?: string
  screenshot?: string
  suspension_id?: string
  run_id?: string
  node_id?: string
  browser_session_id?: string
  tab_id?: string
  resumable?: boolean
  handoff?: BrowserAssistanceHandoff
} | null

export type ChatRuntimePane = {
  key: string
  sessionId: string | null
  messages: RuntimeMessage[]
  running: boolean
  stopping: boolean
  lastActivatedAt: number
  activeStream: TurnStreamHandle | null
  abortController: AbortController | null
  activeAssistantMessageId: string | null
  activeIntervention: RuntimeIntervention
  authResumeController: AbortController | null
  operationId: number
  activeAuthToken: string | null
  executionLocation: 'server' | 'desktop' | 'remote_sandbox'
  runtimePresetId: string
  modelInstanceId: string
  codeProject: { workspace_id: string; git_branch: string; worktree: boolean } | null
  foreignRun: ForeignRun | null
  foreignRunFinished: boolean
  refreshingSession: boolean
}

type SendInput = {
  text: string
  images: File[]
  documents: PendingRuntimeDocument[]
  knowledgeQaEnabled: boolean
  selectedSkillId?: string
  modelId?: string
  onRejected?: () => void
  authToken: string | null
  userId: string | null
  mainId: string | null
  locale: 'zh' | 'en'
  timezone: string
}

type RuntimeCallbacks = {
  onSessionMissing?: (sessionId: string) => void
  onSessionResolved?: (sessionId: string) => void
  onSessionUpdated?: (sessionId: string) => void | Promise<void>
  onQuotaRefresh?: () => void | Promise<void>
  onLoginRequired?: () => void
  onSessionBusy?: () => void
}

export type ExternalTurnHandle = {
  pane: ChatRuntimePane
  assistant: RuntimeMessage
  apply(event: TimelineEventV3): boolean
  finish(): void
  setCodeChanges(changes: DshTaskChangeSet): void
}

const state = reactive({
  panes: [] as ChatRuntimePane[],
  activeKey: '',
  unreadSessionIds: new Set<string>(),
})

let localSeq = 0
let messageSeq = 0
const MAX_CACHED_PANES = 8

function nextLocalKey() {
  localSeq += 1
  return `local_${Date.now()}_${localSeq}`
}

function nextMessageId() {
  messageSeq += 1
  return `msg_${Date.now()}_${messageSeq}`
}

function ensureMessageId<T extends RuntimeMessage>(msg: T): T {
  if (msg._id) return msg
  return { ...msg, _id: nextMessageId() }
}

function normalizeMessages(raw: ChatMessage[] | RuntimeMessage[] | undefined): RuntimeMessage[] {
  if (!raw?.length) return []
  const result: RuntimeMessage[] = []
  for (const item of raw) {
    const role = item.role === 'user' ? 'user' : 'assistant'
    const msg = ensureMessageId({ ...(item as RuntimeMessage), role, content: item.content || '', user_id: item.user_id })
    result.push(msg)
  }
  return result
}

function createPane(input: { key?: string; sessionId: string | null; messages?: ChatMessage[] | RuntimeMessage[]; running?: boolean }): ChatRuntimePane {
  return {
    key: input.key || nextLocalKey(),
    sessionId: input.sessionId,
    messages: normalizeMessages(input.messages),
    running: Boolean(input.running),
    stopping: false,
    lastActivatedAt: Date.now(),
    activeStream: null,
    abortController: null,
    activeAssistantMessageId: null,
    activeIntervention: null,
    authResumeController: null,
    operationId: 0,
    activeAuthToken: null,
    executionLocation: 'server',
    runtimePresetId: 'zy-enterprise',
    modelInstanceId: '',
    codeProject: null,
    foreignRun: null,
    foreignRunFinished: false,
    refreshingSession: false,
  }
}

function updateForeignRun(pane: ChatRuntimePane, activeRun: SessionSummary['active_run'], viewerUserId: string) {
  const next = foreignRunForViewer(activeRun, viewerUserId)
  if (next) {
    if (pane.foreignRun?.messageId !== next.messageId) pane.foreignRunFinished = false
  } else if (pane.foreignRun) {
    pane.foreignRunFinished = true
  }
  pane.foreignRun = next
}

function findPaneByKey(key: string) {
  return state.panes.find((pane) => pane.key === key) || null
}

function findPaneBySessionId(sessionId: string) {
  return state.panes.find((pane) => pane.sessionId === sessionId) || null
}

function activePane() {
  return findPaneByKey(state.activeKey)
}

function setActivePane(pane: ChatRuntimePane) {
  pane.lastActivatedAt = Date.now()
  state.activeKey = pane.key
}

function pruneInactivePanes() {
  const activeKey = state.activeKey
  const mustKeep = new Set<string>()
  for (const pane of state.panes) {
    if (pane.running || pane.key === activeKey) {
      mustKeep.add(pane.key)
    }
  }

  const keepBySession = new Set<string>()
  const sessionPanes = state.panes
    .filter((pane) => pane.sessionId)
    .sort((a, b) => b.lastActivatedAt - a.lastActivatedAt)
  for (const pane of sessionPanes.slice(0, MAX_CACHED_PANES)) {
    keepBySession.add(pane.key)
  }

  state.panes = state.panes.filter((pane) => mustKeep.has(pane.key) || keepBySession.has(pane.key))
}

function clearUnread(sessionId: string | null) {
  if (!sessionId || !state.unreadSessionIds.has(sessionId)) return
  const next = new Set(state.unreadSessionIds)
  next.delete(sessionId)
  state.unreadSessionIds = next
}

// T30: this in-memory set drives the unread mechanism for OWNER rows
// (App.vue's sessionIsUnread). Shared rows take their value from the server's
// shared_unread field through the separate sharedSessions ref and never touch
// this set, so a shared id arriving here (a shared pane stopping while
// inactive) cannot produce a dot there. Do not connect sessionIsUnread into
// the shared dot's condition — the set keeps the id until clearUnread runs,
// which would paint a dot again after the server cursor has been cleared.
function markUnread(sessionId: string) {
  if (!sessionId) return
  const next = new Set(state.unreadSessionIds)
  next.add(sessionId)
  state.unreadSessionIds = next
}

function setPaneRunning(pane: ChatRuntimePane, running: boolean) {
  const wasRunning = pane.running
  pane.running = running
  if (wasRunning && !running && pane.sessionId && pane.key !== state.activeKey) {
    markUnread(pane.sessionId)
  }
  if (!running) pruneInactivePanes()
}

function resolvePaneSession(pane: ChatRuntimePane, sessionId: string, callbacks: RuntimeCallbacks = {}) {
  if (!sessionId) return
  const duplicate = state.panes.find((item) => item !== pane && item.sessionId === sessionId)
  if (duplicate) {
    const keepRunningPane = pane.running || !duplicate.running
    const keeper = keepRunningPane ? pane : duplicate
    const removed = keepRunningPane ? duplicate : pane
    keeper.sessionId = sessionId
    if (pane.modelInstanceId) keeper.modelInstanceId = pane.modelInstanceId
    else if (!keeper.modelInstanceId) keeper.modelInstanceId = duplicate.modelInstanceId
    keeper.lastActivatedAt = Math.max(keeper.lastActivatedAt, removed.lastActivatedAt)
    if (!keeper.messages.length && removed.messages.length) keeper.messages = removed.messages
    if (!keeper.activeStream && removed.activeStream) keeper.activeStream = removed.activeStream
    if (!keeper.abortController && removed.abortController) keeper.abortController = removed.abortController
    if (!keeper.activeAssistantMessageId && removed.activeAssistantMessageId) keeper.activeAssistantMessageId = removed.activeAssistantMessageId
    if (!keeper.activeIntervention && removed.activeIntervention) keeper.activeIntervention = removed.activeIntervention
    if (!keeper.authResumeController && removed.authResumeController) keeper.authResumeController = removed.authResumeController
    if (state.activeKey === removed.key) state.activeKey = keeper.key
    state.panes = state.panes.filter((item) => item !== removed)
    callbacks.onSessionResolved?.(sessionId)
    return
  }
  pane.sessionId = sessionId
  callbacks.onSessionResolved?.(sessionId)
}

function ensureExecV3(msg: RuntimeMessage): ExecutionStoreV3 {
  return ensureMessageExecutionV3(msg)
}

async function uploadImages(userId: string, files: File[], authToken?: string | null): Promise<UploadedImage[]> {
  const uploaded: UploadedImage[] = []
  for (const file of files) {
    uploaded.push(await uploadChatImage(userId, file, authToken))
  }
  return uploaded
}

async function uploadDocuments(userId: string, docs: PendingRuntimeDocument[], authToken?: string | null): Promise<RuntimeDocumentInfo[]> {
  const uploaded: RuntimeDocumentInfo[] = []
  for (const item of docs) {
    const result: UploadedDocument = await uploadChatDocument(userId, item.file, authToken)
    uploaded.push({
      type: item.kind,
      url: result.url || result.signed_url || '',
      filename: result.filename || item.file.name,
      title: result.filename || item.file.name,
      object_path: result.object_path,
      signed_url: result.signed_url,
      content_type: result.content_type,
      size: result.size,
    })
  }
  return uploaded
}

function resetPanePreview(pane: ChatRuntimePane) {
  pane.activeIntervention = null
}

async function sendMessage(key: string, input: SendInput, callbacks: RuntimeCallbacks = {}) {
  const pane = findPaneByKey(key)
  if (!pane) return
  pane.authResumeController?.abort()
  pane.authResumeController = null
  if (pane.foreignRun || pane.foreignRunFinished) {
    input.onRejected?.()
    callbacks.onSessionBusy?.()
    return
  }
  if (pane.running) {
    await stopGeneration(key)
    return
  }

  const text = input.text.trim()
  const hasImages = input.images.length > 0
  const hasDocuments = input.documents.length > 0
  const hasSelectedSkill = !!input.selectedSkillId
  if (!text && !hasImages && !hasDocuments && !hasSelectedSkill) return
  if (!input.authToken || !input.userId || !input.mainId || input.mainId === 'default') {
    callbacks.onLoginRequired?.()
    return
  }

  pane.operationId += 1
  const operationId = pane.operationId
  setPaneRunning(pane, true)
  pane.activeAuthToken = input.authToken
  let uploadedImages: UploadedImage[] = []
  let uploadedDocuments: RuntimeDocumentInfo[] = []
  try {
    const quota = await fetchOrgBilling(input.authToken)
    if (pane.operationId !== operationId) return
    // 配额不可用（后端无此端点）≠ 额度耗尽：仅在服务端明确给出余额时判尽额。
    if (quota.ok && Number(quota.data?.remainingPoints || 0) <= 0) {
      const isEnterprise = quota.data?.spaceType === 'enterprise'
      pane.messages.push({
        _id: nextMessageId(),
        role: 'assistant',
        content: isEnterprise
          ? '当前企业分派额度已用尽，请联系企业管理员调整额度。'
          : '个人赠送额度已用尽，请升级或切换到有可用额度的空间。',
      })
      setPaneRunning(pane, false)
      return
    }

    uploadedImages = await uploadImages(input.userId, input.images, input.authToken)
    if (pane.operationId !== operationId) return
    uploadedDocuments = await uploadDocuments(input.userId, input.documents, input.authToken)
  } catch (uploadErr) {
    pane.messages.push({
      _id: nextMessageId(),
      role: 'assistant',
      content: `[Error: ${input.locale === 'zh' ? '附件上传失败' : 'Could not upload the attachments'}: ${uploadErr}]`,
    })
    setPaneRunning(pane, false)
    pane.activeAuthToken = null
    return
  }
  if (pane.operationId !== operationId) return

  const userMessage: RuntimeMessage = {
    _id: nextMessageId(),
    role: 'user',
    content: text,
    user_id: input.userId || undefined,
    images: uploadedImages as RuntimeImageInfo[],
    documents: uploadedDocuments,
    created_at: new Date().toISOString(),
  }
  pane.messages.push(userMessage)

  const assistantMsg: RuntimeMessage = {
    _id: nextMessageId(),
    role: 'assistant',
    content: '',
  }
  pane.messages.push(assistantMsg)
  pane.activeAssistantMessageId = assistantMsg._id || null
  ensureExecV3(assistantMsg).reset()
  resetPanePreview(pane)

  const ctrl = new AbortController()
  pane.abortController = ctrl
  let activeHandle: TurnStreamHandle | null = null
  let backendEventCursor = 0
  let backendTerminalReceived = false

  const applyAssistantEvent = (ev: TimelineEventV3, options: { fromBackend?: boolean } = {}) => {
    const msg = pane.messages.find((item) => item._id === assistantMsg._id)
    if (!msg || !isTimelineEventV3(ev)) return
    const store = ensureExecV3(msg)
    const before = store.state.rawEvents.length
    store.applyEvent(ev)
    if (
      options.fromBackend &&
      (ev.type === 'run.completed' || ev.type === 'run.failed' || ev.type === 'run.cancelled')
    ) {
      backendTerminalReceived = true
    }
    const accepted = store.state.rawEvents.length > before
    if (accepted && options.fromBackend) {
      const sequence = Number(ev.stream_seq_end || ev.stream_seq || 0)
      backendEventCursor = sequence > 0 ? Math.max(backendEventCursor, sequence) : backendEventCursor + 1
    }
    if (!accepted) return
    applyAssistantContentEvent(msg, ev)
    const transition = browserInterventionTransition(ev)
    if (transition.kind === 'cleared') pane.activeIntervention = null
    if (transition.kind === 'activated') pane.activeIntervention = transition.intervention
  }

  const recoverDisconnectedStream = async (showRecoveryEvent = true): Promise<boolean> => {
    const messageId = assistantMsg.message_id || activeHandle?.messageId || ''
    if (!messageId) return false
    if (showRecoveryEvent) {
      applyAssistantEvent({
        v: 3,
        event_id: `recover_${messageId}`,
        id: `recover_${messageId}`,
        ts: Date.now(),
        type: 'item.completed',
        item_kind: 'commentary',
        item_id: `recover_${messageId}`,
        revision: 1,
        payload: {
          message: input.locale === 'zh' ? '连接中断，正在恢复进度' : 'Connection lost — restoring progress',
        },
      })
    }
    let after = backendEventCursor
    while (!ctrl.signal.aborted) {
      try {
        const recovered = await fetchChatMessageEvents(messageId, after, { authToken: input.authToken })
        for (const ev of recovered.events) applyAssistantEvent(ev, { fromBackend: true })
        after = recovered.next_cursor
        backendEventCursor = recovered.next_cursor
        if (!recovered.live && recovered.status !== 'live') return true
      } catch {
        // The same network outage may take the recovery endpoint down with it.
      }
      await new Promise((resolve) => setTimeout(resolve, 2000))
    }
    return false
  }

  try {
    const handle = startTurnStream(
      {
        modelId: input.modelId || undefined,
        knowledgeQaEnabled: input.knowledgeQaEnabled,
        timezone: input.timezone,
        messages: pane.messages.slice(0, -1).map((m) => ({
          role: m.role,
          content: m.content,
          images: m.images || [],
          documents: m.documents || [],
        })),
        output_spec: {
          user_id: input.userId || undefined,
          main_id: input.mainId || undefined,
          task_id: pane.sessionId || undefined,
          selected_skill_id: input.selectedSkillId || undefined,
          manual_skill_selected: Boolean(input.selectedSkillId) || undefined,
        },
      },
      (ev) => {
        applyAssistantEvent(ev, { fromBackend: true })
      },
      {
        authToken: input.authToken,
        onSessionId: (sid) => {
          if (input.modelId) pane.modelInstanceId = input.modelId
          assistantMsg._backendSid = sid
          resolvePaneSession(pane, sid, callbacks)
        },
        onMessageId: (mid) => {
          assistantMsg.message_id = mid
        },
      },
    )
    activeHandle = handle
    pane.activeStream = handle
    ctrl.signal.addEventListener('abort', () => handle.abort())
    await handle.done
    if (!ctrl.signal.aborted && !backendTerminalReceived) {
      // A proxy is allowed to end a streaming response gracefully. Check that
      // the backend run actually hit a terminal state rather than reading EOF
      // as task completion.
      await recoverDisconnectedStream(true)
    }
    if (pane.sessionId && input.userId && input.authToken) {
      await resumeBrowserInterventionTaskUntilSettled({
        userId: input.userId,
        sessionId: pane.sessionId,
        authToken: input.authToken,
        modelId: input.modelId || undefined,
        locale: input.locale,
        signal: ctrl.signal,
        getIntervention: () => pane.activeIntervention,
        getMessages: () => pane.messages.slice(0, -1).map((m) => ({
          role: m.role,
          content: m.content,
          images: m.images || [],
          documents: m.documents || [],
        })),
        setWaitController: (controller) => { pane.authResumeController = controller },
        setRunning: (running) => setPaneRunning(pane, running),
        setActiveHandle: (resumed) => {
          activeHandle = resumed
          pane.activeStream = resumed
        },
        clearIntervention: () => { pane.activeIntervention = null },
        onEvent: (ev) => applyAssistantEvent(ev, { fromBackend: true }),
        onMessageId: (mid) => {
          backendEventCursor = 0
          backendTerminalReceived = false
          assistantMsg.message_id = mid
        },
      })
    }
  } catch (error: any) {
    if (error instanceof TurnStreamHttpError && error.status === 409 && error.code === TURN_ALREADY_RUNNING) {
      // A concurrent run holds the session (server 409). Drop BOTH optimistic
      // bubbles (the pair pushed above), tell the viewer in a neutral way, and
      // keep the composer usable for another attempt — no error bubble here.
      // message_sequence_conflict and other generic failures go through the
      // regular error handling further down.
      pane.messages = pane.messages.filter(
        (item) => item._id !== userMessage._id && item._id !== assistantMsg._id,
      )
      input.onRejected?.()
      callbacks.onSessionBusy?.()
      if (pane.sessionId && input.userId) {
        try {
          const detail = await getSession(pane.sessionId, input.userId, input.mainId || undefined, input.authToken)
          updateForeignRun(pane, detail.active_run, input.userId)
        } catch {
          // Sidebar polling will settle the visible run state.
        }
      }
    } else if (error?.name !== 'AbortError') {
      const recovered = await recoverDisconnectedStream().catch(() => false)
      if (!recovered && !ctrl.signal.aborted) {
        const errText = String(error?.message || error || (input.locale === 'zh' ? '请求失败' : 'The request did not succeed'))
        const localErrorId = 'err_' + Math.random().toString(36).slice(2)
        ensureExecV3(assistantMsg).applyEvent({
          v: 3,
          event_id: localErrorId,
          id: localErrorId,
          ts: Date.now(),
          type: 'item.failed',
          item_kind: 'error',
          item_id: 'local_error',
          revision: 1,
          payload: { message: errText },
        } as TimelineEventV3)
      }
    }
  } finally {
    if (pane.operationId !== operationId) return
    // The running flag belongs to transport completion. Reset it ahead of any
    // optional refresh callback so that a slow or failing sidebar/billing
    // request cannot leave the composer stuck loading.
    pane.activeStream = null
    pane.abortController = null
    pane.activeAssistantMessageId = null
    if (pane.authResumeController?.signal.aborted) pane.authResumeController = null
    setPaneRunning(pane, false)

    const artifacts = assistantMsg._execV3?.state.artifacts || []
    const docs: RuntimeDocumentInfo[] = artifacts.map((a) => ({
      type: a.kind as RuntimeDocumentInfo['type'],
      url: a.url || '',
      signed_url: a.signed_url,
      object_path: a.object_path,
      filename: a.filename,
      title: a.title,
      content_type: a.content_type,
      size: a.size,
      bundle: a.bundle,
    }))
    if (docs.length) assistantMsg.documents = docs
    assistantMsg.execution_events = assistantMsg._execV3?.state.rawEvents || undefined
    assistantMsg.evidence_bundles = assistantMsg._execV3?.state.proofPacks || undefined

    const resolvedSessionId = assistantMsg._backendSid || pane.sessionId
    await refreshAfterRun(callbacks, resolvedSessionId)
  }
}

async function stopGeneration(key: string) {
  const pane = findPaneByKey(key)
  if (!pane) return false
  return await stopTurnGeneration(pane, (running) => setPaneRunning(pane, running))
}

export function useTurnRuntimeStore(callbacks: RuntimeCallbacks = {}) {
  const panes = computed(() => state.panes)
  const activeChatKey = computed(() => state.activeKey)
  const activeChatPane = computed(() => activePane())
  const runningSessionIds = computed(() => {
    const ids = new Set<string>()
    for (const pane of state.panes) {
      if (pane.running && pane.sessionId) ids.add(pane.sessionId)
    }
    return ids
  })
  const unreadSessionIdSet = computed(() => state.unreadSessionIds)
  const currentSessionId = computed(() => activePane()?.sessionId || null)

  function reset() {
    for (const pane of state.panes) {
      pane.activeStream?.abort()
      pane.abortController?.abort()
      pane.authResumeController?.abort()
    }
    state.panes = []
    state.activeKey = ''
    state.unreadSessionIds = new Set()
  }

  function startLocalSession() {
    const pane = createPane({ sessionId: null })
    state.panes = [...state.panes, pane]
    setActivePane(pane)
    pruneInactivePanes()
    return pane
  }

  async function selectSession(sessionId: string, userId: string, mainId?: string, authToken?: string | null) {
    clearUnread(sessionId)
    const existing = findPaneBySessionId(sessionId)
    if (existing) {
      setActivePane(existing)
      return existing
    }
    const detail: SessionDetail = await getSession(sessionId, userId, mainId, authToken)
    const pane = createPane({
      key: `session_${detail.id}`,
      sessionId: detail.id,
      messages: detail.messages || [],
    })
    pane.executionLocation = detail.execution_location || 'server'
    pane.runtimePresetId = detail.runtime_preset_id || 'zy-enterprise'
    pane.modelInstanceId = detail.model_instance_id || ''
    pane.codeProject = detail.code_project || null
    updateForeignRun(pane, detail.active_run, userId)
    state.panes = [...state.panes, pane]
    setActivePane(pane)
    pruneInactivePanes()
    if (detail.active_run?.message_id && authToken) {
      const assistant = [...pane.messages].reverse().find(
        (item) => item.role === 'assistant' && item.message_id === detail.active_run?.message_id,
      )
      if (assistant) {
        // Todo 29: the stop control is the composer's running button, visible
        // only while the pane runs. A foreign run — one belonging to another
        // member, or a legacy run with no recorded initiator — must never offer
        // stop: the server rejects cancel from anyone but the initiator (403
        // session_cancel_initiator_required; fail-closed for legacy rows).
        // For every viewer, the poll below keeps tracking the run's progress.
        const runInitiatorUserId = detail.active_run.initiator_user_id || ''
        const restoredStore = ensureExecV3(assistant)
        pane.activeIntervention = normalizeBrowserIntervention(restoredStore.state.intervention)
        const controller = new AbortController()
        pane.abortController = controller
        pane.activeAssistantMessageId = assistant._id || null
        if (runInitiatorUserId === userId) setPaneRunning(pane, true)
        void (async () => {
          const store = restoredStore
          store.resumeLive()
          let cursor = Math.max(0, ...store.state.rawEvents.map((event) => Number(event.stream_seq_end || event.stream_seq || 0)))
          try {
            try {
              while (!controller.signal.aborted) {
                const recovered = await fetchChatMessageEvents(detail.active_run!.message_id, cursor, { authToken })
                for (const event of recovered.events) {
                  if (!isTimelineEventV3(event)) continue
                  store.applyEvent(event)
                  applyAssistantContentEvent(assistant, event)
                  const transition = browserInterventionTransition(event)
                  if (transition.kind === 'cleared') pane.activeIntervention = null
                  if (transition.kind === 'activated') pane.activeIntervention = transition.intervention
                }
                cursor = recovered.next_cursor
                if (!recovered.live && recovered.status !== 'live') break
                await new Promise((resolve) => setTimeout(resolve, 2000))
              }
            } catch {
              // Sidebar polling and a later re-open give another recovery route.
            }
            if (pane.activeIntervention) {
              await resumeBrowserInterventionTaskUntilSettled({
                userId,
                sessionId: detail.id,
                authToken,
                locale: getLocale(),
                signal: controller.signal,
                getIntervention: () => pane.activeIntervention,
                getMessages: () => pane.messages,
                setWaitController: (waitController) => { pane.authResumeController = waitController },
                setRunning: (running) => setPaneRunning(pane, running),
                setActiveHandle: (handle) => { pane.activeStream = handle },
                clearIntervention: () => { pane.activeIntervention = null },
                onEvent: (event) => {
                  store.applyEvent(event)
                  applyAssistantContentEvent(assistant, event)
                  const transition = browserInterventionTransition(event)
                  if (transition.kind === 'cleared') pane.activeIntervention = null
                  if (transition.kind === 'activated') pane.activeIntervention = transition.intervention
                },
                onMessageId: (messageId) => { assistant.message_id = messageId },
              })
            }
          } finally {
            if (pane.abortController === controller) pane.abortController = null
            pane.activeAssistantMessageId = null
            setPaneRunning(pane, false)
            await refreshAfterRun(callbacks, pane.sessionId)
          }
        })()
      }
    }
    return pane
  }

  function syncActiveRuns(summaries: SessionSummary[], viewerUserId: string) {
    for (const summary of summaries) {
      const pane = findPaneBySessionId(summary.id)
      if (pane && !pane.running) updateForeignRun(pane, summary.active_run, viewerUserId)
    }
  }

  async function refreshSession(sessionId: string, userId: string, mainId?: string, authToken?: string | null) {
    const pane = findPaneBySessionId(sessionId)
    if (!pane || pane.running || pane.refreshingSession) return
    pane.refreshingSession = true
    try {
      const detail = await getSession(sessionId, userId, mainId, authToken)
      pane.messages = normalizeMessages(detail.messages)
      updateForeignRun(pane, detail.active_run, userId)
      pane.foreignRunFinished = false
      clearUnread(sessionId)
    } finally {
      pane.refreshingSession = false
    }
  }

  function removeSession(sessionId: string) {
    findPaneBySessionId(sessionId)?.authResumeController?.abort()
    state.panes = state.panes.filter((pane) => pane.sessionId !== sessionId)
    clearUnread(sessionId)
    if (currentSessionId.value === sessionId) {
      startLocalSession()
    }
  }

  function sessionIsRunning(sessionId: string) {
    return runningSessionIds.value.has(sessionId)
  }

  function sessionIsUnread(sessionId: string) {
    return unreadSessionIdSet.value.has(sessionId)
  }

  function clearPaneIntervention(key: string) {
    const pane = findPaneByKey(key)
    if (pane) pane.activeIntervention = null
  }

  function setPanePreviewExpanded(_key: string, _expanded: boolean) {
    // Retained as an API boundary for a future sidecar persistence layer; for
    // now expansion stays local to ConversationWindow since it is visual-only
    // state.
  }

  /**
   * A renderer-neutral boundary for a Runtime owned by the desktop main
   * process. Intentionally shares the authoritative message and V3 execution
   * stores; local Code must not sprout a second chat/timeline implementation.
   */
  function beginExternalTurn(key: string, text: string, sessionId: string): ExternalTurnHandle {
    const pane = findPaneByKey(key)
    if (!pane) throw new Error('chat pane could not be found')
    if (pane.running) throw new Error('chat pane already has a turn in progress')
    resolvePaneSession(pane, sessionId, callbacks)
    pane.executionLocation = 'desktop'
    pane.runtimePresetId = 'code'
    const user: RuntimeMessage = {
      _id: nextMessageId(), role: 'user', content: text, created_at: new Date().toISOString(),
    }
    const assistant: RuntimeMessage = { _id: nextMessageId(), role: 'assistant', content: '' }
    pane.messages.push(user, assistant)
    pane.activeAssistantMessageId = assistant._id || null
    ensureExecV3(assistant).reset()
    resetPanePreview(pane)
    setPaneRunning(pane, true)
    return {
      pane,
      assistant,
      apply(event: TimelineEventV3) {
        if (!isTimelineEventV3(event)) return false
        const store = ensureExecV3(assistant)
        const before = store.state.rawEvents.length
        store.applyEvent(event)
        if (store.state.rawEvents.length === before) return false
        applyAssistantContentEvent(assistant, event)
        return true
      },
      finish() {
        pane.activeAssistantMessageId = null
        setPaneRunning(pane, false)
        void refreshAfterRun(callbacks, pane.sessionId)
      },
      setCodeChanges(changes: DshTaskChangeSet) {
        const target = pane.messages.find(message => message._id === assistant._id)
        if (target) target._codeChanges = changes
      },
    }
  }

  return {
    panes,
    activeChatKey,
    activeChatPane,
    currentSessionId,
    runningSessionIds,
    unreadSessionIdSet,
    reset,
    startLocalSession,
    selectSession,
    syncActiveRuns,
    refreshSession,
    removeSession,
    sessionIsRunning,
    sessionIsUnread,
    clearUnread,
    clearPaneIntervention,
    setPanePreviewExpanded,
    beginExternalTurn,
    sendMessage: (key: string, input: SendInput) => sendMessage(key, input, callbacks),
    stopGeneration,
  }
}
