<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import type { ExecutionStoreV3 } from '../stores/timelineStore'
import { t, useLocale } from '../../../hooks/i18n'
import CommentaryItem from './CommentaryItem.vue'
import ProvisionalAssistantItem from './ProvisionalAssistantItem.vue'
import ActivityItem from './ActivityItem.vue'
import RepeatedToolCallGroup from './RepeatedToolCallGroup.vue'
import { hasVisibleActiveRunningLeaf, runningContainerIds } from '../domain/activityPresentation'
import { collapseRepeatedToolCalls } from '../domain/repeatedToolCalls'
import { elapsedRunMs, formatRunDuration } from '../domain/runTiming'

const props = defineProps<{ store: ExecutionStoreV3; live?: boolean }>()
const emit = defineEmits<{
  (event: 'resolve-permission', requestId: string, decision: 'allow' | 'deny' | 'always_allow'): void
}>()
const collapsed = ref(false)
const userOverrode = ref(false)
const { locale } = useLocale()
const clock = ref(Date.now())
let clockHandle: ReturnType<typeof setInterval> | null = null
const rows = computed(() => {
  // 兼容本次部署/HMR 周期之前创建的 store：此时 computed ref 可能已
  // 经由响应式消息被 Vue 自动解包。
  const source = props.store.visibleItems as any
  const items = Array.isArray(source) ? source : source?.value
  return (Array.isArray(items) ? items : []).filter(
    (item) => !['artifact', 'evidence', 'browser_preview'].includes(item.kind)
      && (item.kind !== 'commentary' || String(item.payload?.text || '').trim()),
  )
})
const passiveRunningIds = computed(() => runningContainerIds(rows.value))
const timeline = computed(() => collapseRepeatedToolCalls(rows.value))
const showIdlePlaceholder = computed(
  () => Boolean(props.live)
    && !hasVisibleActiveRunningLeaf(rows.value, passiveRunningIds.value),
)
const terminalWithoutAnswer = computed(
  () => !props.live && ['failed', 'cancelled', 'blocked'].includes(props.store.state.runStatus),
)
// 本轮确实跑过（收到过 run.started / 答案已完成）：回复完成后保留折叠条，
// 避免无工具调用的纯对话回复整块卸载、连"已处理"折叠条都不留。
// 历史消息无执行事件时（runStartedAt 为空）保持隐藏，不给旧消息凭空加条。
const runHappened = computed(() =>
  props.store.state.runStartedAt !== null
    || props.store.state.finalAnswerComplete
    || props.store.state.runStatus === 'completed',
)
const terminalText = computed(() => {
  if (props.store.state.runStatus === 'blocked') return t('execution.v3.blocked')
  if (props.store.state.runStatus === 'cancelled') return t('execution.v3.cancelled')
  return t('execution.v3.failed')
})
const terminalDetail = computed(() => {
  if (props.store.state.runStatus !== 'failed') return ''
  const failedEvent = [...props.store.state.rawEvents].reverse().find(event => event.type === 'run.failed')
  const message = String(failedEvent?.payload?.message || '').trim()
  if (!message) return ''
  return /fetch failed|failed to fetch/i.test(message)
    ? t('execution.v3.failed_network')
    : message
})
const activityCount = computed(() => rows.value.filter(
  (item) => !['commentary', 'final_answer'].includes(item.kind),
).length)
const elapsedText = computed(() => formatRunDuration(
  elapsedRunMs(props.store.state.runStartedAt, props.store.state.runEndedAt, clock.value),
  locale.value,
))
const toggleText = computed(() => {
  if (!collapsed.value) return t('execution.v3.process')
  return activityCount.value
    ? t('execution.v3.processed_count', { count: activityCount.value })
    : t('execution.v3.processed')
})

watch(() => props.store.state.finalAnswerComplete, (done) => {
  if (done && !userOverrode.value) collapsed.value = true
}, { immediate: true })

watch(
  () => Boolean(props.live) && props.store.state.runStatus === 'running' && props.store.state.runStartedAt !== null,
  (running) => {
    if (clockHandle !== null) clearInterval(clockHandle)
    clockHandle = null
    clock.value = Date.now()
    if (running) clockHandle = setInterval(() => { clock.value = Date.now() }, 1000)
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  if (clockHandle !== null) clearInterval(clockHandle)
})

function toggle() {
  collapsed.value = !collapsed.value
  userOverrode.value = true
}
</script>

<template>
  <section v-if="rows.length || live || terminalWithoutAnswer || runHappened" class="timeline-v3" :class="{ collapsed }">
    <button class="timeline-v3-toggle" type="button" :aria-expanded="!collapsed" @click="toggle">
      <svg viewBox="0 0 24 24"><path d="m9 18 6-6-6-6" /></svg>
      <span>{{ toggleText }}</span>
      <time v-if="elapsedText" class="timeline-v3-elapsed" aria-live="off">· {{ elapsedText }}</time>
    </button>
    <div v-show="!collapsed" class="timeline-v3-list">
      <template v-for="entry in timeline" :key="entry.key">
        <RepeatedToolCallGroup
          v-if="entry.type === 'tool-group'"
          :items="entry.items"
          :status-items="entry.statusItems"
        />
        <CommentaryItem v-else-if="entry.item.kind === 'commentary'" :item="entry.item" />
        <ProvisionalAssistantItem v-else-if="entry.item.kind === 'final_answer'" :item="entry.item" />
        <ActivityItem
          v-else
          :item="entry.item"
          :active="!passiveRunningIds.has(entry.item.id)"
          @resolve-permission="(requestId, decision) => emit('resolve-permission', requestId, decision)"
        />
      </template>
      <div v-if="showIdlePlaceholder" class="timeline-v3-placeholder" role="status" :aria-label="t('execution.v3.processing')">
        <span class="timeline-v3-thinking">{{ t('execution.v3.thinking') }}</span>
      </div>
      <div v-if="terminalWithoutAnswer" class="timeline-v3-terminal" role="status">
        <span>{{ terminalText }}</span>
        <small v-if="terminalDetail">{{ terminalDetail }}</small>
      </div>
    </div>
  </section>
</template>

<style scoped>
.timeline-v3 { display:flex; flex-direction:column; gap:6px; }
.timeline-v3-toggle { display:inline-flex; width:fit-content; min-height:32px; align-items:center; gap:5px; padding:2px 4px; border:0; background:transparent; color:#64748b; cursor:pointer; font-size:13px; font-weight:600; }
.timeline-v3-toggle:focus-visible { outline:2px solid #2563eb; outline-offset:2px; border-radius:6px; }
.timeline-v3-toggle svg { width:15px; height:15px; fill:none; stroke:currentColor; stroke-width:2; stroke-linecap:round; stroke-linejoin:round; transform:rotate(90deg); transition:transform .18s ease; }
.timeline-v3-elapsed { min-width:4.5em; color:#7c8798; font-variant-numeric:tabular-nums; font-weight:500; }
.collapsed .timeline-v3-toggle svg { transform:rotate(0deg); }
.timeline-v3-list { display:flex; flex-direction:column; gap:4px; padding:3px 4px 7px; }
.timeline-v3-placeholder { display:flex; align-items:center; min-height:28px; }
.timeline-v3-thinking {
  color:transparent;
  background:linear-gradient(100deg,#64748b 12%,#2563eb 42%,#93c5fd 52%,#2563eb 62%,#64748b 88%);
  background-size:240% 100%;
  background-clip:text;
  -webkit-background-clip:text;
  font-size:12px;
  font-weight:600;
  animation:thinking-sheen 1.7s linear infinite;
}
.timeline-v3-terminal { display:flex; flex-direction:column; gap:3px; color:#b45309; font-size:13px; padding-bottom:5px; }
.timeline-v3-terminal small { color:#7c5b27; font-size:12px; font-weight:400; }
@keyframes thinking-sheen { from { background-position:100% 0; } to { background-position:-140% 0; } }
@media (prefers-reduced-motion: reduce) {
  .timeline-v3-toggle svg { transition:none; }
  .timeline-v3-thinking { color:#2563eb; background:none; animation:none; }
}
</style>
