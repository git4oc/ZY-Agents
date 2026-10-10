<template>
  <section v-if="items.length" class="updates">
    <header><strong>{{ t('skills.update.title') }}</strong><n-tag type="warning" size="small" round>{{ items.length }}</n-tag></header>
    <article v-for="item in items" :key="item.id">
      <div><strong>{{ item.name }}</strong><p>{{ item.currentVersion || '-' }} → {{ item.newVersion }}</p><small v-if="item.releaseNotes">{{ item.releaseNotes }}</small><small v-if="item.localModified" class="warning">{{ t('skills.update.local_changes') }}</small></div>
      <n-button type="primary" secondary size="small" @click="review(item)">{{ t('skills.update.action') }}</n-button>
    </article>
    <SkillUpdateDialog v-model:show="dialogVisible" :item="selected" :busy="Boolean(selected && busy.has(selected.id))" @confirm="confirmUpdate" />
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { NButton, NTag, useMessage } from 'naive-ui'
import { fetchSkillUpdates, installSkillUpdate, type SkillUpdateNotice } from '../../api/skillSharing'
import { t } from '../../hooks/i18n'
import SkillUpdateDialog from './SkillUpdateDialog.vue'

const emit = defineEmits<{ count: [value: number]; installed: [] }>()
const message = useMessage()
const items = ref<SkillUpdateNotice[]>([])
const busy = ref(new Set<string>())
const selected = ref<SkillUpdateNotice | null>(null)
const dialogVisible = ref(false)
async function load() {
  const data = await fetchSkillUpdates()
  items.value = data.items || []
  emit('count', Number(data.pendingCount) || 0)
}
async function performInstall(item: SkillUpdateNotice, confirmReplace = false) {
  busy.value = new Set([...busy.value, item.id])
  try { await installSkillUpdate(item.id, confirmReplace); message.success(t('skills.update.success')); dialogVisible.value = false; selected.value = null; await load(); emit('installed') }
  catch { message.error(t('skills.update.failed')) }
  finally { const next = new Set(busy.value); next.delete(item.id); busy.value = next }
}
function review(item: SkillUpdateNotice) { selected.value = item; dialogVisible.value = true }
function confirmUpdate(item: SkillUpdateNotice) { void performInstall(item, item.localModified) }
onMounted(() => void load())
defineExpose({ load })
</script>

<style scoped>
.updates{display:grid;gap:10px;margin-bottom:16px;padding:14px;border:1px solid #fde4b2;border-radius:12px;background:#fffaf0}.updates header,.updates article{display:flex;align-items:center;justify-content:space-between;gap:12px}.updates header{justify-content:flex-start}.updates article{padding:12px;border-radius:9px;background:#fff}.updates p,.updates small{display:block;margin:3px 0 0;color:#78859a}.updates small{max-width:680px;white-space:pre-wrap}.updates .warning{color:#b54708}
</style>
