<template>
  <header class="flex h-12 shrink-0 items-center justify-between gap-3 border-b border-gray-200 bg-white px-4">
    <span class="min-w-0 flex-1 truncate text-[13px] font-semibold text-slate-700" :title="headerTitle">{{ headerTitle }}</span>
    <div class="flex shrink-0 items-center gap-1">
      <button
        v-if="detail && (viewerMode === 'participant' || canShare)"
        type="button"
        class="relative grid h-9 w-9 place-items-center rounded-lg border-0 bg-transparent text-slate-600 transition-colors hover:bg-gray-100 hover:text-blue-600 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-200"
        :aria-label="shareControlLabel"
        :title="shareControlLabel"
        @click="shareDialogOpen = true"
      >
        <svg v-if="viewerMode === 'owner' && participantCount === 0" class="h-[18px] w-[18px]" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="M4 12v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8"/><polyline points="16 6 12 2 8 6"/><line x1="12" x2="12" y1="2" y2="15"/>
        </svg>
        <svg v-else class="h-[18px] w-[18px]" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>
        </svg>
        <span v-if="participantCount > 0" class="absolute top-px right-px min-w-[14px] rounded-full border border-white bg-blue-600 px-[3px] text-center text-[8px] leading-[12px] text-white">{{ participantCount > 99 ? '99+' : participantCount }}</span>
      </button>
    </div>
    <SessionShareDialog
      :show="shareDialogOpen"
      :mode="viewerMode"
      :session-id="sessionId"
      @update:show="shareDialogOpen = $event"
      @removed="reloadDetail"
      @revoked="reloadDetail"
      @left="onLeft"
    />
  </header>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import SessionShareDialog from '../session/SessionShareDialog.vue'
import { getSession, type SessionDetail } from '../../api/sessions'
import { t } from '../../hooks/i18n'

const props = defineProps<{
  sessionId: string | null
  /** 标题取自父组件预取的会话列表，与桌面外壳读取的是同一份数据。 */
  title: string
  /** 明细请求中标识当前查看者的 userId 查询参数；服务端真正用于解析查看者身份的是 bearer token。 */
  userId: string
  mainId: string
  authToken: string | null
}>()

// 事件接线：组件把 "left" 交给父组件（App.vue），由父组件清理离开的会话
// 面板并刷新会话列表；本随之卸载。
const emit = defineEmits<{ left: [sessionId: string] }>()
// 运行时契约：GET /sessions/{id} 返回的是查看者视角的 access、owner_user_id、
// participant_count 与 execution_location，同时覆盖"查看自己的会话"与
// "参与者查看共享会话"两种情形。TS 类型尚未同步（待把这些字段上移到
// SessionSummary），因此通过本地的交叉类型读取；字段可选，SessionDetail
// 仍可赋值，无需强制转换。
type SessionSharingDetail = SessionDetail & {
  access?: 'owner' | 'shared'
  owner_user_id?: string
  participant_count?: number
}

const detail = ref<SessionSharingDetail | null>(null)
const shareDialogOpen = ref(false)
let detailSequence = 0

watch(() => props.sessionId, (sessionId) => {
  detailSequence += 1
  detail.value = null
  shareDialogOpen.value = false
  if (!sessionId) return
  void loadDetail(sessionId)
}, { immediate: true })

async function loadDetail(sessionId: string) {
  const sequence = detailSequence
  try {
    const loaded = await getSession(sessionId, props.userId, props.mainId || undefined, props.authToken)
    if (sequence !== detailSequence) return
    detail.value = loaded
  } catch {
    // 查看者无权访问该会话（已被移除、401 或网络失败）——头部只渲染标题；
    // 真正的权限判定在服务端。
    if (sequence !== detailSequence) return
    detail.value = null
  }
}

// detail.access 反映查看者自身的权限。缺失时回退为 owner，
// "owner" — the historical default; any stale-payload bypass is stopped by the
// 最终判定仍以服务端 403（session_share_owner_required）为准。
const viewerMode = computed<'owner' | 'participant'>(() => (
  detail.value?.access === 'shared' ? 'participant' : 'owner'
))

// 仅取自服务端载荷（明细中的 active 非 owner 参与者行数），
// 绝不在客户端自行推算。
const participantCount = computed(() => detail.value?.participant_count ?? 0)
const shareControlLabel = computed(() => (
  viewerMode.value === 'owner' && participantCount.value === 0
    ? t('session.share.action')
    : t('session.share.participants_count', { count: participantCount.value })
))

// 可共享性为尽力而为：session_runtime_context 经由查看者范围的绑定查询
// 传递 execution_location，因此对非最后发言者的查看者通常缺失。
// 缺失即视为未知 → 显示图标；上报为 desktop /
// remote_sandbox 时隐藏。最终判定仍以服务端 409
// （session_share_no_binding / session_share_not_server）为准。
const canShare = computed(() => {
  if (viewerMode.value !== 'owner') return false
  const location = detail.value?.execution_location
  if (!location) return true
  return location === 'server'
})

const headerTitle = computed(() => detail.value?.title || props.title || '')

// 事件接线：弹窗中的变更会影响头部可见数据——移除/撤销后需刷新
// 参与者数（通过链接入场的带外加入同样会受该变更影响），
// 故重新拉取会话明细；"left" 事件交给父组件，
// 由其清理面板（头部随之卸载）。
function reloadDetail() {
  if (!props.sessionId) return
  void loadDetail(props.sessionId)
}

function onLeft() {
  const sessionId = props.sessionId
  if (!sessionId) return
  emit('left', sessionId)
}
</script>
