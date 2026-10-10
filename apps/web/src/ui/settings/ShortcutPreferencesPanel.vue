<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { NAlert, NButton, NForm, NFormItem, NInput, NModal, NSelect, NSpace, NSpin, useMessage } from 'naive-ui'
import { fetchEffectiveShortcuts, saveShortcutPreferences, type ShortcutEntry, type ShortcutEntryType, type ShortcutGroup, type ShortcutPreferences } from '../../api/shortcuts'
import { fetchSelectableSkills } from '../../api/skills'
import { effectiveShortcutEntries } from '../chat/shortcutGuideResolver'
import { t } from '../../hooks/i18n'
import ShortcutPreferenceGroup from './ShortcutPreferenceGroup.vue'

const props = defineProps<{ userId: string | null; mainId: string }>()
const message = useMessage()
const loading = ref(false)
const saving = ref(false)
const groups = ref<ShortcutGroup[]>([])
const entries = ref<ShortcutEntry[]>([])
const editorOpen = ref(false)
const groupEditorOpen = ref(false)
const groupName = ref('')
const draft = ref({ type: 'prompt' as ShortcutEntryType, label: '', prompt: '', resourceId: '' })
const skillOptions = ref<Array<{ label: string; value: string }>>([])
const skillLoading = ref(false)
const hasPersonalGroup = computed(() => groups.value.some(group => group.personal))
const typeOptions = computed(() => [
  { label: t('shortcuts.type_prompt'), value: 'prompt' },
  { label: t('shortcuts.type_skill'), value: 'skill' },
])
const canAdd = computed(() => !!draft.value.label.trim() && (draft.value.type === 'skill' ? !!draft.value.resourceId : !!draft.value.prompt.trim()))

onMounted(load)
watch(() => draft.value.type, type => {
  if (type === 'skill') void searchSkills('')
  else draft.value.resourceId = ''
})

async function searchSkills(keyword: string) {
  if (!props.userId) return
  skillLoading.value = true
  try {
    const result = await fetchSelectableSkills({ userId: props.userId, mainId: props.mainId, keyword, limit: 50 })
    skillOptions.value = result.items.map(item => ({ label: item.name, value: item.id }))
  } catch { skillOptions.value = [] }
  finally { skillLoading.value = false }
}

async function load() {
  loading.value = true
  try {
    const result = await fetchEffectiveShortcuts()
    entries.value = effectiveShortcutEntries(result).map(item => ({ ...item }))
    const derivedGroups = [...new Map(entries.value.map(item => [item.categoryKey, {
      key: item.categoryKey, label: item.categoryLabel, iconKey: item.categoryIconKey || 'grid', locked: false,
    }])).values()]
    groups.value = result.configured
      ? (result.groups.length ? result.groups.map(group => ({ ...group })) : derivedGroups)
      : [...derivedGroups.filter(group => group.key !== 'personal'), ...result.groups.filter(group => group.key === 'personal').map(group => ({ ...group }))]
  } catch { message.error(t('shortcuts.loading_failed')) }
  finally { loading.value = false }
}

function groupEntries(key: string) { return entries.value.filter(item => item.categoryKey === key) }

function move(key: string, index: number, offset: number) {
  const group = groups.value.find(item => item.key === key)
  if (!group || group.locked) return
  const members = groupEntries(key)
  const other = members[index + offset]
  if (!other) return
  const firstIndex = entries.value.findIndex(item => item.id === members[index].id)
  const secondIndex = entries.value.findIndex(item => item.id === other.id)
  ;[entries.value[firstIndex], entries.value[secondIndex]] = [entries.value[secondIndex], entries.value[firstIndex]]
}

function createGroup() {
  if (!groupName.value.trim()) return
  const existing = groups.value.find(group => group.personal)
  if (existing) {
    existing.label = groupName.value.trim()
    entries.value = entries.value.map(item => item.categoryKey === 'personal' ? { ...item, categoryLabel: existing.label } : item)
  } else groups.value.push({ key: 'personal', label: groupName.value.trim(), iconKey: 'grid', personal: true })
  groupEditorOpen.value = false
}

function renameGroup() {
  groupName.value = groups.value.find(group => group.personal)?.label || ''
  groupEditorOpen.value = true
}

function removeGroup() {
  entries.value = entries.value.filter(item => item.categoryKey !== 'personal')
  groups.value = groups.value.filter(item => item.key !== 'personal')
}

function addPersonal() {
  if (!hasPersonalGroup.value || groupEntries('personal').length >= 6 || !canAdd.value) return
  const group = groups.value.find(item => item.key === 'personal')!
  entries.value.push({
    id: `personal.${Date.now()}`, type: draft.value.type, categoryKey: 'personal', categoryLabel: group.label,
    label: draft.value.label.trim(), iconKey: draft.value.type === 'skill' ? 'build' : 'document',
    prompt: draft.value.prompt.trim(), resourceId: draft.value.resourceId, enabled: true, personal: true,
  })
  draft.value = { type: 'prompt', label: '', prompt: '', resourceId: '' }
  editorOpen.value = false
}

function selectSkill(value: string, option: { label?: string } | null) {
  draft.value.resourceId = value
  if (option?.label) draft.value.label = option.label
}

function payload(): ShortcutPreferences {
  const personalGroup = groups.value.find(group => group.personal) || null
  return {
    groupOrders: Object.fromEntries(groups.value.filter(group => !group.locked).map(group => [group.key, groupEntries(group.key).map(item => item.id)])),
    personalGroup,
    personalEntries: entries.value.filter(item => item.personal),
  }
}

async function persist(value: ShortcutPreferences) {
  saving.value = true
  try {
    await saveShortcutPreferences(value)
    await load()
    window.dispatchEvent(new CustomEvent('shortcut-preferences-updated'))
    message.success(t('shortcuts.saved'))
  } catch { message.error(t('shortcuts.save_failed')) }
  finally { saving.value = false }
}

async function reset() { await persist({ groupOrders: {}, personalGroup: null, personalEntries: [] }) }
</script>

<template>
  <section class="shortcut-panel">
    <header class="panel-head">
      <div><h3>{{ t('shortcuts.title') }}</h3><p>{{ t('shortcuts.description') }}</p></div>
      <n-button v-if="!hasPersonalGroup" size="small" secondary @click="groupName = ''; groupEditorOpen = true">{{ t('shortcuts.create_group') }}</n-button>
    </header>
    <n-alert type="info" :show-icon="false" :bordered="false">{{ t('shortcuts.required_hint') }}</n-alert>
    <n-spin :show="loading"><div class="group-list">
      <ShortcutPreferenceGroup v-for="group in groups" :key="group.key" :group="group" :entries="groupEntries(group.key)"
        @move="move" @remove="id => entries = entries.filter(item => item.id !== id)"
        @add="editorOpen = true" @rename="renameGroup" @remove-group="removeGroup" />
    </div></n-spin>
    <footer><n-space><n-button class="shortcut-action-button" size="small" :loading="saving" @click="reset">{{ t('shortcuts.reset') }}</n-button><n-button class="shortcut-action-button" size="small" type="primary" :loading="saving" @click="persist(payload())">{{ t('shortcuts.save') }}</n-button></n-space></footer>

    <n-modal v-model:show="groupEditorOpen" preset="card" :title="t(hasPersonalGroup ? 'shortcuts.rename_group' : 'shortcuts.create_group')" style="width:min(420px,calc(100vw - 32px))">
      <n-form-item :label="t('shortcuts.group_name')"><n-input v-model:value="groupName" :maxlength="80" /></n-form-item>
      <template #footer><n-button type="primary" :disabled="!groupName.trim()" @click="createGroup">{{ t('shortcuts.create_group') }}</n-button></template>
    </n-modal>
    <n-modal v-model:show="editorOpen" preset="card" :title="t('shortcuts.add')" style="width:min(520px,calc(100vw - 32px))">
      <n-form label-placement="top">
        <n-form-item :label="t('shortcuts.type')"><n-select v-model:value="draft.type" :options="typeOptions" /></n-form-item>
        <n-form-item :label="t('shortcuts.name')" required><n-input v-model:value="draft.label" :maxlength="80" /></n-form-item>
        <n-form-item v-if="draft.type === 'skill'" :label="t('shortcuts.select_skill')" required>
          <n-select :value="draft.resourceId || null" :options="skillOptions" :loading="skillLoading" filterable remote @search="searchSkills" @focus="searchSkills('')" @update:value="selectSkill" />
        </n-form-item>
        <n-form-item :label="t('shortcuts.prompt')" :required="draft.type === 'prompt'"><n-input v-model:value="draft.prompt" type="textarea" :rows="4" /></n-form-item>
      </n-form>
      <template #footer><n-space justify="end"><n-button @click="editorOpen = false">{{ t('ui.cancel') }}</n-button><n-button type="primary" :disabled="!canAdd" @click="addPersonal">{{ t('shortcuts.add') }}</n-button></n-space></template>
    </n-modal>
  </section>
</template>

<style scoped>
.shortcut-panel,.group-list{display:grid;gap:12px}.panel-head,footer{display:flex;justify-content:space-between;align-items:center;gap:12px}.panel-head h3{margin:0;font-size:16px;color:#182236}.panel-head p{margin:4px 0 0;color:#748099;font-size:12px}footer{justify-content:flex-end;padding-top:4px}.group-list{max-height:50vh;overflow:auto;padding-right:4px}.shortcut-panel :deep(button),.shortcut-panel :deep(.n-button),.shortcut-panel :deep(.n-button__content){cursor:pointer!important}.shortcut-panel :deep(button:disabled),.shortcut-panel :deep(.n-button--disabled),.shortcut-panel :deep(.n-button--disabled .n-button__content){cursor:not-allowed!important}
.shortcut-panel :deep(.shortcut-action-button){cursor:pointer!important}.shortcut-panel :deep(.shortcut-action-button:hover){background:#eef4ff;color:#2563eb}.shortcut-panel :deep(.n-button--primary-type.shortcut-action-button:hover){color:#fff;background:#1d4ed8}
</style>
