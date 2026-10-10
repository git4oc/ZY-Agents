<template>
  <SkillUpdatesPanel @count="handleUpdateCount" @installed="emit('installed')" />
  <n-spin :show="loading">
    <div v-if="items.length" class="inbox-grid">
      <article v-for="item in items" :key="item.deliveryId" class="inbox-card">
        <div class="inbox-head">
          <div class="sender-avatar">{{ senderInitial(item) }}</div>
          <div class="sender-copy">
            <strong>{{ item.sender.displayName || item.sender.username || t('skills.share.unknown_sender') }}</strong>
            <span>{{ t('skills.share.shared_at', { time: formatAppDateTime(item.sharedAt, '') }) }}</span>
          </div>
          <n-tag size="small" :bordered="false" type="info">{{ typeText(item.type) }}</n-tag>
        </div>
        <div class="skill-name">{{ item.name }}</div>
        <div class="skill-description">{{ item.description || t('skills.no_desc') }}</div>
        <div class="skill-meta">
          <span>v{{ item.version }}</span>
          <span>{{ t('skills.install_file_count', { count: item.fileCount }) }}</span>
          <span v-if="item.childCount">{{ t('skills.install_child_count', { count: item.childCount }) }}</span>
        </div>
        <n-alert v-if="item.hasConflict" type="warning" :show-icon="false" class="compact-alert">{{ t('skills.share.conflict_hint') }}</n-alert>
        <n-alert v-else-if="item.alreadyInstalled" type="success" :show-icon="false" class="compact-alert">{{ t('skills.share.already_installed') }}</n-alert>
        <div class="inbox-actions">
          <n-button text :disabled="busyIds.has(item.deliveryId)" @click="decline(item)">{{ t('skills.share.decline') }}</n-button>
          <n-button type="primary" :loading="busyIds.has(item.deliveryId)" @click="accept(item)">
            {{ item.hasConflict ? t('skills.share.replace') : item.alreadyInstalled ? t('skills.share.confirm_installed') : t('skills.share.accept_install') }}
          </n-button>
        </div>
      </article>
    </div>
    <n-empty v-else-if="!loading" :description="t('skills.share.inbox_empty')" class="inbox-empty" />
    <div v-if="hasMore" class="load-more"><n-button secondary :loading="loadingMore" @click="load(true)">{{ t('ui.load_more') }}</n-button></div>
  </n-spin>
</template>

<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { NAlert, NButton, NEmpty, NSpin, NTag, useDialog, useMessage } from 'naive-ui'
import type { SkillType } from '../../api/skills'
import { acceptReceivedSkillShare, declineReceivedSkillShare, fetchReceivedSkillShares, type ReceivedSkillShare } from '../../api/skillSharing'
import { formatAppDateTime } from '../../hooks/appTimezone'
import { t } from '../../hooks/i18n'
import { skillShareErrorMessage } from './skillShareErrors'
import SkillUpdatesPanel from './SkillUpdatesPanel.vue'

const props = defineProps<{ active: boolean; scopeKey: string }>()
const emit = defineEmits<{ installed: []; 'count-change': [count: number] }>()
const message = useMessage()
const dialog = useDialog()
const items = ref<ReceivedSkillShare[]>([])
const loading = ref(false)
const loadingMore = ref(false)
const cursor = ref('')
const hasMore = ref(false)
const pendingCount = ref(0)
const updateCount = ref(0)
const busyIds = ref(new Set<string>())
let loadVersion = 0

function emitTotal() { emit('count-change', pendingCount.value + updateCount.value) }
function handleUpdateCount(value: number) { updateCount.value = value; emitTotal() }

function typeText(type: SkillType) {
  if (type === 'expert_package') return t('skills.type.expert_package')
  if (type === 'workflow') return t('skills.type.workflow')
  if (type === 'writing_style') return t('skills.type.style')
  return t('skills.type.ordinary')
}
function senderInitial(item: ReceivedSkillShare) { return (item.sender.displayName || item.sender.username || 'S').slice(0, 1).toUpperCase() }

async function load(append = false) {
  const version = ++loadVersion
  const scopeKey = props.scopeKey
  append ? loadingMore.value = true : loading.value = true
  try {
    const page = await fetchReceivedSkillShares(append ? cursor.value : '', 20)
    if (version !== loadVersion || scopeKey !== props.scopeKey) return
    items.value = append ? [...items.value, ...page.items] : page.items
    cursor.value = page.nextCursor
    hasMore.value = page.hasMore
    pendingCount.value = page.pendingCount
    emitTotal()
  } catch (error: any) {
    if (version === loadVersion && scopeKey === props.scopeKey) {
      message.error(skillShareErrorMessage(error, 'skills.share.inbox_failed'))
    }
  } finally {
    if (version === loadVersion) {
      loading.value = false
      loadingMore.value = false
    }
  }
}

function setBusy(id: string, busy: boolean) {
  const next = new Set(busyIds.value)
  busy ? next.add(id) : next.delete(id)
  busyIds.value = next
}

async function performAccept(item: ReceivedSkillShare) {
  setBusy(item.deliveryId, true)
  try {
    await acceptReceivedSkillShare(item.deliveryId, item.hasConflict)
    message.success(t('skills.share.install_success'))
    items.value = items.value.filter((row) => row.deliveryId !== item.deliveryId)
    pendingCount.value = Math.max(0, pendingCount.value - 1)
    emitTotal()
    emit('installed')
  } catch (error: any) { message.error(skillShareErrorMessage(error, 'skills.share.install_failed')) }
  finally { setBusy(item.deliveryId, false) }
}

function accept(item: ReceivedSkillShare) {
  if (!item.hasConflict) return void performAccept(item)
  dialog.warning({
    title: t('skills.share.conflict_title'), content: t('skills.share.conflict_hint'),
    positiveText: t('skills.share.replace'), negativeText: t('ui.cancel'),
    onPositiveClick: () => performAccept(item),
  })
}

function decline(item: ReceivedSkillShare) {
  dialog.warning({
    title: t('skills.share.decline_title'), content: t('skills.share.decline_hint', { name: item.name }),
    positiveText: t('skills.share.decline'), negativeText: t('ui.cancel'),
    onPositiveClick: async () => {
      setBusy(item.deliveryId, true)
      try {
        await declineReceivedSkillShare(item.deliveryId)
        items.value = items.value.filter((row) => row.deliveryId !== item.deliveryId)
        pendingCount.value = Math.max(0, pendingCount.value - 1)
        emitTotal()
      } catch (error: any) { message.error(skillShareErrorMessage(error, 'skills.share.decline_failed')) }
      finally { setBusy(item.deliveryId, false) }
    },
  })
}

onMounted(() => void load())
watch(() => props.active, () => { if (props.active) void load() })
watch(() => props.scopeKey, () => {
  loadVersion += 1
  items.value = []
  cursor.value = ''
  hasMore.value = false
  pendingCount.value = 0
  emitTotal()
  void load()
})
</script>

<style scoped>
.inbox-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 14px; }
.inbox-card { min-height: 230px; padding: 17px; display: flex; flex-direction: column; gap: 11px; border: 1px solid #e4e9f2; border-radius: 12px; background: #fff; box-shadow: 0 6px 18px rgba(23, 43, 82, 0.055); }
.inbox-head { display: flex; align-items: center; gap: 10px; }
.sender-avatar { width: 34px; height: 34px; display: grid; place-items: center; flex: 0 0 34px; border-radius: 50%; color: #2459e8; background: #e8efff; font-size: 13px; font-weight: 800; }
.sender-copy { min-width: 0; flex: 1; display: grid; gap: 2px; }
.sender-copy strong { overflow: hidden; color: #344054; font-size: 13px; text-overflow: ellipsis; white-space: nowrap; }
.sender-copy span { color: #98a2b3; font-size: 11px; }
.skill-name { color: #17233d; font-size: 17px; font-weight: 800; }
.skill-description { color: #667085; font-size: 13px; line-height: 1.55; display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2; overflow: hidden; }
.skill-meta { display: flex; gap: 14px; color: #8490a3; font-size: 12px; }
.compact-alert { font-size: 12px; }
.inbox-actions { margin-top: auto; display: flex; justify-content: flex-end; align-items: center; gap: 14px; }
.inbox-empty { padding: 90px 0; }
.load-more { padding: 18px 0 2px; text-align: center; }
</style>
