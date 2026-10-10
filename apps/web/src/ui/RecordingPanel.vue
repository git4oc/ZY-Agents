<script setup lang="ts">
// Line B: recording panel. User enters a start URL, clicks "开始录制",
// 打开一个带界面的浏览器，由用户走一遍流程，每次点击/输入都会以
// RecordingEvent 流回。完成后用户可复核步骤列表（删除、把取值改为变量），
// 再提交——emit('done', steps) 会把步骤追加到上层编辑器。
//
// Variable-ize: click the "变量" button next to a fill step's value
// 指令中会以 ${name} 取代该字面量，同一个 name 同时成为
// locator_vars 中可编辑的默认值。一次录制由此变成参数化技能。

import { computed, onUnmounted, ref } from 'vue'
import { NButton, NInput, NInputGroup, NSpace, NTag } from 'naive-ui'
import type { CompositeStep } from '../api/compositeSkill'
import { t } from '../hooks/i18n'
import {
  type RecordingEvent,
  type StreamHandle,
  eventsToSteps,
  startRecording,
  stopRecording,
  streamRecording,
} from '../api/recording'

const props = defineProps<{
  userId: string | null
  siteProfileId?: string
}>()
const emit = defineEmits(['done', 'cancel'])

const startUrl = ref<string>('https://')
const events = ref<RecordingEvent[]>([])
const recording = ref<boolean>(false)
const recordingId = ref<string>('')
const stream = ref<StreamHandle | null>(null)
const errMsg = ref<string>('')
// 编辑操作：``delete`` 移除事件；``variable`` 把一次填入改标为变量。
const deletedIdx = ref<Set<number>>(new Set())
const varByIdx = ref<Record<number, string>>({})

const steps = computed<CompositeStep[]>(() => {
  const live = events.value.filter((_, i) => !deletedIdx.value.has(i))
  const out = eventsToSteps(live, props.siteProfileId || '')
  // 应用变量化：把指令中的填入字面量替换为变量引用。
  let liveIdx = 0
  for (let i = 0; i < events.value.length; i++) {
    if (deletedIdx.value.has(i)) continue
    const varName = varByIdx.value[i]
    if (varName && events.value[i].type === 'fill' && out[liveIdx]) {
      out[liveIdx].instruction = `在 ${events.value[i].target?.name || '输入框'} 输入 \${${varName}}`
      out[liveIdx].locator_vars = { ...(out[liveIdx].locator_vars || {}), [varName]: events.value[i].value || '' }
    }
    liveIdx++
  }
  return out
})

async function onStart() {
  if (!props.userId) return
  errMsg.value = ''
  events.value = []
  deletedIdx.value = new Set()
  varByIdx.value = {}
  try {
    const result = await startRecording(props.userId, startUrl.value.trim())
    if (!result.ok) { errMsg.value = t('recording.agent_not_connected'); return }
    recordingId.value = result.recording_id
    recording.value = true
    stream.value = streamRecording(
      props.userId,
      (ev) => { events.value.push(ev) },
      (e) => { errMsg.value = t('recording.stream_interrupted', { message: String((e as any)?.message || e) }) },
    )
  } catch (e: any) {
    errMsg.value = String(e?.message || e)
  }
}

async function onStop() {
  if (!props.userId) return
  recording.value = false
  try { await stopRecording(props.userId, 'default', recordingId.value) } catch {}
  stream.value?.close()
  stream.value = null
}

function toggleDelete(i: number) {
  const s = new Set(deletedIdx.value)
  if (s.has(i)) s.delete(i); else s.add(i)
  deletedIdx.value = s
}

function onSetVar(i: number) {
  const name = prompt(t('recording.var_prompt'), varByIdx.value[i] || 'value')
  if (!name) return
  varByIdx.value = { ...varByIdx.value, [i]: name.trim() }
}

function clearVar(i: number) {
  const next = { ...varByIdx.value }
  delete next[i]
  varByIdx.value = next
}

function onDone() {
  if (recording.value) void onStop()
  emit('done', steps.value)
}

function onCancel() {
  if (recording.value) void onStop()
  emit('cancel')
}

onUnmounted(() => { stream.value?.close(); if (recording.value && props.userId) void stopRecording(props.userId, 'default', recordingId.value) })

function eventLabel(ev: RecordingEvent): string {
  const n = ev.target?.name || ev.target?.text || ev.target?.aria_label || ''
  if (ev.type === 'click') return n ? t('recording.event.click', { name: n }) : t('recording.event.click_fallback')
  if (ev.type === 'fill') return t('recording.event.fill', { val: (ev.value || '').slice(0, 30), name: n || t('recording.event.fill_fallback') })
  if (ev.type === 'select') return t('recording.event.select', { name: n || t('recording.event.select_fallback') })
  if (ev.type === 'navigate') return t('recording.event.navigate', { url: ev.url || '' })
  return ev.type
}
</script>

<template>
  <div class="space-y-4">
    <div>
      <div class="text-sm font-semibold text-gray-700 mb-1">{{ t('recording.start_url') }}</div>
      <n-input-group>
        <n-input
          v-model:value="startUrl"
          size="small"
          placeholder="https://..."
          :disabled="recording"
        />
        <n-button v-if="!recording" size="small" type="primary" :disabled="!props.userId" @click="onStart">
          {{ t('recording.start_record') }}
        </n-button>
        <n-button v-else size="small" type="error" @click="onStop">
          {{ t('recording.stop_record') }}
        </n-button>
      </n-input-group>
      <div v-if="recording" class="text-xs text-amber-600 mt-1">
        {{ t('recording.browser_hint') }}
      </div>
      <div v-if="errMsg" class="text-xs text-red-600 mt-1">{{ errMsg }}</div>
    </div>

    <!-- Live event list -->
    <div class="border border-gray-200 rounded-lg bg-white">
      <div class="px-3 py-2 border-b border-gray-100 flex items-center justify-between">
        <span class="text-sm font-semibold text-gray-700">{{ t('recording.events_title', { count: events.length }) }}</span>
        <span class="text-xs text-gray-400">{{ t('recording.events_hint') }}</span>
      </div>
      <div v-if="!events.length" class="px-3 py-6 text-center text-xs text-gray-400">
        {{ t('recording.no_events') }}
      </div>
      <ul v-else class="divide-y divide-gray-100 max-h-[320px] overflow-y-auto">
        <li
          v-for="(ev, i) in events"
          :key="i"
          class="px-3 py-2 flex items-center gap-2"
          :class="deletedIdx.has(i) ? 'opacity-40 line-through' : ''"
        >
          <n-tag size="small" :bordered="false" :type="ev.type === 'click' ? 'info' : ev.type === 'fill' ? 'success' : 'default'">
            {{ ev.type }}
          </n-tag>
          <span class="flex-1 text-sm text-gray-700 truncate">{{ eventLabel(ev) }}</span>
          <n-tag v-if="varByIdx[i]" size="small" type="warning" :bordered="false">
            ${{ varByIdx[i] }}
          </n-tag>
          <n-space :size="4">
            <n-button
              v-if="ev.type === 'fill'"
              size="tiny"
              quaternary
              :type="varByIdx[i] ? 'warning' : 'default'"
              @click="varByIdx[i] ? clearVar(i) : onSetVar(i)"
            >
              {{ varByIdx[i] ? t('recording.remove_var') : t('recording.add_var') }}
            </n-button>
            <n-button size="tiny" quaternary type="error" @click="toggleDelete(i)">
              {{ deletedIdx.has(i) ? t('recording.restore') : t('ui.delete') }}
            </n-button>
          </n-space>
        </li>
      </ul>
    </div>

    <!-- Preview -->
    <div v-if="steps.length" class="border border-dashed border-blue-200 bg-blue-50/40 rounded-lg p-3">
      <div class="text-xs font-semibold text-blue-700 mb-1">{{ t('recording.steps_will_generate', { count: steps.length }) }}</div>
      <ol class="list-decimal list-inside text-xs text-gray-700 space-y-0.5">
        <li v-for="(s, i) in steps" :key="i" class="truncate">{{ s.instruction }}</li>
      </ol>
    </div>

    <div class="flex justify-end gap-2 pt-1">
      <n-button size="small" @click="onCancel">{{ t('ui.cancel') }}</n-button>
      <n-button size="small" type="primary" :disabled="!steps.length" @click="onDone">
        {{ t('recording.apply_to_skill', { count: steps.length }) }}
      </n-button>
    </div>
  </div>
</template>
