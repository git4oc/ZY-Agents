<template>
  <n-modal :show="show" preset="card" :title="t('skills.share.title')" style="width: min(600px, calc(100vw - 32px))" @update:show="emit('update:show', $event)">
    <div class="share-content">
      <div class="skill-summary">
        <div class="skill-mark">S</div>
        <div><strong>{{ skill?.name }}</strong><span>{{ skill?.description || t('skills.no_desc') }}</span></div>
      </div>
      <div>
        <div class="section-label">{{ t('skills.share.choose_members') }}</div>
        <n-select
          v-model:value="selectedIds" multiple filterable remote clearable
          :options="memberOptions" :loading="memberLoading"
          :placeholder="t('skills.share.member_placeholder')" :max-tag-count="3"
          @search="scheduleSearch"
        >
          <template #action>
            <div class="select-action">
              <span>{{ t('skills.share.selected_count', { count: selectedIds.length }) }}</span>
              <n-button v-if="hasMoreMembers" text type="primary" :loading="loadingMore" @click="loadMoreMembers">{{ t('ui.load_more') }}</n-button>
            </div>
          </template>
        </n-select>
        <p class="hint">{{ t('skills.share.direct_hint') }}</p>
      </div>
      <n-alert v-if="sharedCount" type="success" :show-icon="true" :title="t('skills.share.sent')">
        {{ t('skills.share.sent_hint', { count: sharedCount }) }}
      </n-alert>
      <n-collapse class="more-methods">
        <n-collapse-item :title="t('skills.share.more_methods')" name="link">
          <template v-if="!created">
            <div class="link-settings">
              <n-select v-model:value="expiresInDays" :options="expiryOptions" size="small" />
              <n-button secondary size="small" :loading="creating" @click="createLink">{{ t('skills.share.create') }}</n-button>
            </div>
            <p class="hint">{{ t('skills.share.link_hint') }}</p>
          </template>
          <template v-else>
            <n-input ref="shareLinkInput" :value="shareUrl" readonly size="small">
              <template #suffix><n-button text type="primary" @click="copyLink">{{ t('skills.share.copy') }}</n-button></template>
            </n-input>
            <n-button text type="error" size="tiny" class="revoke-link" :loading="revoking" @click="revoke">{{ t('skills.share.revoke') }}</n-button>
          </template>
        </n-collapse-item>
      </n-collapse>
    </div>
    <template #footer>
      <n-space justify="end">
        <n-button @click="emit('update:show', false)">{{ t('ui.close') }}</n-button>
        <n-button type="primary" :disabled="!selectedIds.length" :loading="sending" @click="sendToUsers">{{ t('skills.share.send_action') }}</n-button>
      </n-space>
    </template>
  </n-modal>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { NAlert, NButton, NCollapse, NCollapseItem, NInput, NModal, NSelect, NSpace, useMessage } from 'naive-ui'
import type { SkillItem } from '../../api/skills'
import { createSkillShare, revokeSkillShare, searchSkillShareMembers, shareSkillWithUsers, type SkillShareCreated, type SkillShareMember } from '../../api/skillSharing'
import { t } from '../../hooks/i18n'
import { copyTextToClipboard } from '../../lib/copyTextToClipboard'
import { skillShareErrorMessage } from './skillShareErrors'

const props = defineProps<{ show: boolean; skill: SkillItem | null }>()
const emit = defineEmits<{ 'update:show': [value: boolean]; shared: [] }>()
const message = useMessage()
const shareLinkInput = ref<InstanceType<typeof NInput> | null>(null)
const selectedIds = ref<string[]>([])
const members = ref<SkillShareMember[]>([])
const memberLoading = ref(false)
const loadingMore = ref(false)
const memberCursor = ref('')
const hasMoreMembers = ref(false)
const currentKeyword = ref('')
const sending = ref(false)
const sharedCount = ref(0)
const expiresInDays = ref(30)
const creating = ref(false)
const revoking = ref(false)
const created = ref<SkillShareCreated | null>(null)
let searchTimer: ReturnType<typeof setTimeout> | null = null
let searchSequence = 0

const memberOptions = computed(() => members.value.map((member) => ({
  value: member.userId,
  label: [member.displayName, member.username || member.email].filter(Boolean).join(' · '),
})))
const expiryOptions = computed(() => [
  { label: t('skills.share.expiry_7'), value: 7 }, { label: t('skills.share.expiry_30'), value: 30 }, { label: t('skills.share.expiry_90'), value: 90 },
])
const shareUrl = computed(() => {
  if (!created.value || typeof window === 'undefined') return ''
  const url = new URL('/skills', window.location.origin)
  url.searchParams.set('share', created.value.token)
  return url.toString()
})

watch(() => [props.show, props.skill?.id], () => {
  if (!props.show) return
  selectedIds.value = []
  sharedCount.value = 0
  created.value = null
  void loadMembers('', false)
})

function scheduleSearch(keyword: string) {
  currentKeyword.value = keyword.trim()
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(() => void loadMembers(currentKeyword.value, false), 280)
}

async function loadMembers(keyword: string, append: boolean) {
  const sequence = ++searchSequence
  append ? loadingMore.value = true : memberLoading.value = true
  try {
    const page = await searchSkillShareMembers({ keyword, cursor: append ? memberCursor.value : '', limit: 30 })
    if (sequence !== searchSequence) return
    const selected = members.value.filter((item) => selectedIds.value.includes(item.userId))
    const next = append ? [...members.value, ...page.items] : [...selected, ...page.items]
    members.value = Array.from(new Map(next.map((item) => [item.userId, item])).values())
    memberCursor.value = page.nextCursor
    hasMoreMembers.value = page.hasMore
  } catch (error: any) {
    message.error(skillShareErrorMessage(error, 'skills.share.members_failed'))
  } finally {
    if (sequence === searchSequence) {
      memberLoading.value = false
      loadingMore.value = false
    }
  }
}

function loadMoreMembers() { void loadMembers(currentKeyword.value, true) }

async function sendToUsers() {
  if (!props.skill || !selectedIds.value.length) return
  sending.value = true
  try {
    const result = await shareSkillWithUsers(props.skill.id, selectedIds.value)
    sharedCount.value = result.recipientCount
    selectedIds.value = []
    emit('shared')
  } catch (error: any) {
    message.error(skillShareErrorMessage(error, 'skills.share.send_failed'))
  } finally { sending.value = false }
}

async function createLink() {
  if (!props.skill) return
  creating.value = true
  try { created.value = await createSkillShare(props.skill.id, expiresInDays.value); emit('shared') }
  catch (error: any) { message.error(skillShareErrorMessage(error, 'skills.share.create_failed')) }
  finally { creating.value = false }
}

async function copyLink() {
  try { await copyTextToClipboard(shareUrl.value); message.success(t('skills.share.copied')) }
  catch {
    shareLinkInput.value?.focus()
    shareLinkInput.value?.select()
    message.error(t('skills.share.copy_failed'))
  }
}

async function revoke() {
  if (!props.skill || !created.value) return
  revoking.value = true
  try { await revokeSkillShare(props.skill.id, created.value.shareId); message.success(t('skills.share.revoked')); created.value = null }
  catch (error: any) { message.error(skillShareErrorMessage(error, 'skills.share.revoke_failed')) }
  finally { revoking.value = false }
}
</script>

<style scoped>
.share-content { display: grid; gap: 20px; }
.skill-summary { display: flex; gap: 12px; padding: 14px 16px; border: 1px solid #e9edf5; border-radius: 12px; background: #f8faff; }
.skill-summary > div:last-child { min-width: 0; display: grid; gap: 5px; }
.skill-summary strong { color: #18233b; font-size: 15px; }
.skill-summary span { color: #667085; line-height: 1.5; }
.skill-mark { width: 36px; height: 36px; flex: 0 0 36px; display: grid; place-items: center; border-radius: 10px; color: #2459e8; background: #e8efff; font-weight: 800; }
.section-label { margin-bottom: 8px; color: #344054; font-size: 13px; font-weight: 700; }
.hint { margin: 7px 0 0; color: #7a8699; font-size: 12px; line-height: 1.55; }
.select-action, .link-settings { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.select-action { width: 100%; min-width: 0; color: #7a8699; font-size: 12px; }
.link-settings :deep(.n-select) { width: 150px; }
.more-methods { padding-top: 2px; border-top: 1px solid #eef1f6; }
.revoke-link { margin-top: 8px; }
</style>
