<template>
  <div class="view-tabs" role="tablist">
    <button type="button" role="tab" :aria-selected="modelValue === 'mine'" :class="{ active: modelValue === 'mine' }" @click="emit('update:modelValue', 'mine')">
      {{ t('skills.share.my_skills_tab') }}
    </button>
    <button type="button" role="tab" :aria-selected="modelValue === 'shared'" :class="{ active: modelValue === 'shared' }" @click="emit('update:modelValue', 'shared')">
      {{ t('skills.share.inbox_tab') }}
      <span v-if="pendingCount" class="pending-badge">{{ pendingCount > 99 ? '99+' : pendingCount }}</span>
    </button>
  </div>
</template>

<script setup lang="ts">
import { t } from '../../hooks/i18n'

defineProps<{ modelValue: 'mine' | 'shared'; pendingCount: number }>()
const emit = defineEmits<{ 'update:modelValue': [value: 'mine' | 'shared'] }>()
</script>

<style scoped>
.view-tabs { display: flex; align-items: center; gap: 4px; margin-bottom: 14px; padding-bottom: 10px; border-bottom: 1px solid #edf1f7; }
.view-tabs button { position: relative; padding: 8px 14px; border: 0; border-radius: 8px; color: #667085; background: transparent; font-size: 14px; font-weight: 650; cursor: pointer; }
.view-tabs button:hover { color: #344054; background: #f5f7fb; }
.view-tabs button.active { color: #2459e8; background: #edf3ff; }
.pending-badge { min-width: 18px; height: 18px; margin-left: 5px; padding: 0 5px; display: inline-grid; place-items: center; border-radius: 9px; color: #fff; background: #e5484d; font-size: 10px; line-height: 1; }
</style>
