<script setup lang="ts">
import { t } from '../../hooks/i18n'

defineProps<{
  running: boolean
  speakerName: string
  refreshing: boolean
}>()

const emit = defineEmits<{ (event: 'refresh'): void }>()
</script>

<template>
  <div
    class="mx-auto mb-3 flex w-full max-w-4xl items-center justify-between gap-3 rounded-xl border px-4 py-3 text-sm shadow-sm"
    :class="running ? 'run-notice-active border-amber-300 bg-amber-100 text-amber-950' : 'border-emerald-200 bg-emerald-50 text-emerald-900'"
    role="status"
    aria-live="polite"
  >
    <div class="flex min-w-0 items-center gap-2">
      <span v-if="running" class="h-2 w-2 shrink-0 animate-pulse rounded-full bg-amber-500" aria-hidden="true"></span>
      <span>{{ running
        ? t('session.run.other_running', { name: speakerName })
        : t('session.run.finished_refresh') }}</span>
    </div>
    <button
      v-if="!running"
      type="button"
      class="shrink-0 rounded-lg bg-emerald-700 px-3 py-1.5 font-medium text-white hover:bg-emerald-800 disabled:opacity-60"
      :disabled="refreshing"
      @click="emit('refresh')"
    >
      {{ refreshing ? t('session.run.refreshing') : t('session.run.refresh') }}
    </button>
  </div>
</template>

<style scoped>
:global(html.theme-dark) .run-notice-active {
  background-color: rgba(245, 158, 11, 0.18);
  border-color: rgba(251, 191, 36, 0.45);
  color: #fef3c7;
}
</style>
