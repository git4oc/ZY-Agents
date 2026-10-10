<script setup lang="ts">
import { computed } from 'vue'
import { NButton, NTooltip, useMessage } from 'naive-ui'

/**
 * ObjectId 单元格：截断显示（前 4 后 4）+ hover 显示完整值 + 一键复制。
 * 完整 24 位 id 对使用者没有信息量，挤占名称列宽度——长 ID 不再直接铺满列。
 */
const props = defineProps<{ id: string }>()

const message = useMessage()

const short = computed(() => {
  if (!props.id) return '—'
  return props.id.length > 12 ? `${props.id.slice(0, 4)}…${props.id.slice(-4)}` : props.id
})

async function copy(): Promise<void> {
  try {
    await navigator.clipboard.writeText(props.id)
    message.success(`已复制：${props.id}`)
  } catch {
    // 剪贴板不可用（非 https / 无权限）时静默，用户仍可在 tooltip 里看完整值
  }
}
</script>

<template>
  <n-tooltip trigger="hover" placement="top">
    <template #trigger>
      <span class="mono text-[12px]" style="cursor: copy">{{ short }}</span>
    </template>
    <div style="display: flex; align-items: center; gap: 6px">
      <span class="mono">{{ props.id || '—' }}</span>
      <n-button v-if="props.id" size="tiny" quaternary @click="copy">复制</n-button>
    </div>
  </n-tooltip>
</template>
