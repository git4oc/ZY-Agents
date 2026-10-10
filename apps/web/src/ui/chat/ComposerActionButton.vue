<script setup lang="ts">
const props = defineProps<{
  running: boolean
  stopping?: boolean
  disabled?: boolean
  locale: 'zh' | 'en'
}>()

const emit = defineEmits<{
  (event: 'activate'): void
}>()

function actionLabel() {
  if (props.stopping) return props.locale === 'zh' ? '正在停止…' : 'Stopping…'
  if (props.running) return props.locale === 'zh' ? '停止' : 'Stop'
  return props.locale === 'zh' ? '发送' : 'Send'
}
</script>

<template>
  <div class="composer-action">
    <svg v-if="running" class="composer-action__ring" viewBox="0 0 44 44" aria-hidden="true">
      <rect x="3" y="3" width="38" height="38" rx="11" fill="none" stroke="rgba(59,130,246,0.2)" stroke-width="2.5" />
      <rect
        class="composer-action__ring-dash"
        x="3"
        y="3"
        width="38"
        height="38"
        rx="11"
        fill="none"
        stroke="rgba(59,130,246,0.95)"
        stroke-width="2.5"
        stroke-linecap="round"
        stroke-linejoin="round"
        pathLength="100"
        stroke-dasharray="18 90"
      />
    </svg>
    <button
      type="button"
      class="composer-action__button"
      :class="disabled ? 'bg-gray-200 cursor-not-allowed' : 'bg-blue-600 hover:bg-blue-700 shadow-md shadow-blue-200'"
      :disabled="disabled"
      :title="actionLabel()"
      :aria-label="actionLabel()"
      @click="emit('activate')"
    >
      <svg v-if="running && !stopping" width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
        <rect x="6" y="6" width="12" height="12" rx="2" />
      </svg>
      <svg v-else-if="stopping" class="h-4 w-4 animate-spin" viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="3" opacity="0.3" />
        <path d="M21 12a9 9 0 0 0-9-9" stroke="currentColor" stroke-width="3" stroke-linecap="round" />
      </svg>
      <svg v-else width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <path d="M7 11L12 6L17 11M12 18V7" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
      </svg>
    </button>
  </div>
</template>

<style scoped>
.composer-action {
  position: relative;
  display: flex;
  width: 40px;
  height: 40px;
  align-items: center;
  justify-content: center;
}
.composer-action__ring {
  position: absolute;
  inset: 0;
  width: 40px;
  height: 40px;
  pointer-events: none;
}
.composer-action__ring-dash { animation: composer-ring-loop 1.2s linear infinite; }
.composer-action__button {
  position: relative;
  z-index: 1;
  display: flex;
  width: 32px;
  height: 32px;
  align-items: center;
  justify-content: center;
  border-radius: 8px;
  color: white;
  transition: background-color 200ms ease, box-shadow 200ms ease, transform 200ms ease;
}
.composer-action__button:active:not(:disabled) { transform: scale(0.95); }
@keyframes composer-ring-loop { to { stroke-dashoffset: -100; } }
@media (prefers-reduced-motion: reduce) {
  .composer-action__ring-dash { animation: none; }
}
</style>
