<template>
  <n-modal
    :show="show"
    preset="card"
    :title="t('knowledge.move_knowledge')"
    style="width:min(560px,calc(100vw - 32px))"
    @update:show="emit('update:show',$event)"
  >
    <p class="intro">{{ t('knowledge.move_knowledge_intro',{name:item?.name || t('knowledge.default_name')}) }}</p>
    <n-input v-model:value="keyword" clearable :placeholder="t('knowledge.search_directory')">
      <template #prefix><n-icon><SearchOutline /></n-icon></template>
    </n-input>
    <div class="tree-frame">
      <n-tree
        v-if="hasMatches"
        block-line
        virtual-scroll
        :data="treeOptions"
        :pattern="keyword"
        :filter="filterDirectory"
        :show-irrelevant-nodes="false"
        :selected-keys="[selectedKey]"
        :expanded-keys="expandedKeys"
        :render-prefix="renderPrefix"
        @update:selected-keys="selectTarget"
        @update:expanded-keys="keys=>expandedKeys=keys.map(String)"
      />
      <n-empty v-else size="small" :description="t('knowledge.no_matching_directory')" />
    </div>
    <div class="target"><span>{{ t('knowledge.move_to') }}</span><strong>{{ selectedLabel }}</strong></div>
    <template #footer>
      <n-space justify="end">
        <n-button @click="emit('update:show',false)">{{ t('knowledge.cancel') }}</n-button>
        <n-button type="primary" :disabled="!changed" :loading="saving" @click="submit">
          <template #icon><n-icon><MoveOutline /></n-icon></template>{{ t('knowledge.confirm_move') }}
        </n-button>
      </n-space>
    </template>
  </n-modal>
</template>

<script setup lang="ts">
import { computed, h, ref, watch } from 'vue'
import { NButton, NEmpty, NIcon, NInput, NModal, NSpace, NTree, useMessage, type TreeOption } from 'naive-ui'
import FileTrayOutline from '@vicons/ionicons5/es/FileTrayOutline'
import FolderOpenOutline from '@vicons/ionicons5/es/FolderOpenOutline'
import MoveOutline from '@vicons/ionicons5/es/MoveOutline'
import SearchOutline from '@vicons/ionicons5/es/SearchOutline'
import { updatePersonalKnowledge, type KnowledgeDirectory, type PersonalKnowledge } from '../../api/personalKnowledge'
import { t } from '../../hooks/i18n'

const uncategorizedKey = '__uncategorized__'
const props = defineProps<{ show: boolean; item: PersonalKnowledge | null; directories: KnowledgeDirectory[] }>()
const emit = defineEmits<{ 'update:show': [value: boolean]; moved: [] }>()
const message = useMessage()
const keyword = ref('')
const selectedKey = ref(uncategorizedKey)
const expandedKeys = ref<string[]>([])
const saving = ref(false)

type DirectoryOption = TreeOption & { path: string; kind: 'directory' | 'uncategorized' }

const treeOptions = computed<DirectoryOption[]>(() => [
  { key: uncategorizedKey, label: t('knowledge.uncategorized'), path: t('knowledge.uncategorized'), kind: 'uncategorized' },
  ...toOptions(props.directories),
])

const optionByKey = computed(() => {
  const result = new Map<string, DirectoryOption>()
  const walk = (rows: DirectoryOption[]) => rows.forEach(row => {
    result.set(String(row.key), row)
    walk((row.children || []) as DirectoryOption[])
  })
  walk(treeOptions.value)
  return result
})

const selectedLabel = computed(() => optionByKey.value.get(selectedKey.value)?.path || t('knowledge.uncategorized'))
const currentKey = computed(() => props.item?.directoryId || uncategorizedKey)
const changed = computed(() => Boolean(props.item && selectedKey.value !== currentKey.value))
const hasMatches = computed(() => {
  const pattern = keyword.value.trim().toLocaleLowerCase()
  if (!pattern) return true
  return Array.from(optionByKey.value.values()).some(item => item.path.toLocaleLowerCase().includes(pattern))
})

function toOptions(rows: KnowledgeDirectory[], parents: string[] = []): DirectoryOption[] {
  return rows.map(row => {
    const path = [...parents, row.name]
    return {
      key: row.id,
      label: row.name,
      path: path.join(' / '),
      kind: 'directory',
      children: toOptions(row.children || [], path),
    }
  })
}

function filterDirectory(pattern: string, option: TreeOption) {
  return String((option as DirectoryOption).path || option.label || '').toLocaleLowerCase().includes(pattern.trim().toLocaleLowerCase())
}

function renderPrefix({ option }: { option: TreeOption }) {
  const icon = (option as DirectoryOption).kind === 'uncategorized' ? FileTrayOutline : FolderOpenOutline
  return h(NIcon, { size: 18 }, { default: () => h(icon) })
}

function selectTarget(keys: Array<string | number>) {
  if (keys.length) selectedKey.value = String(keys[0])
}

function ancestorsOf(targetId: string, rows: KnowledgeDirectory[], parents: string[] = []): string[] {
  for (const row of rows) {
    if (row.id === targetId) return parents
    const nested = ancestorsOf(targetId, row.children || [], [...parents, row.id])
    if (nested.length) return nested
  }
  return []
}

async function submit() {
  if (!props.item || !changed.value) return
  saving.value = true
  try {
    const directoryId = selectedKey.value === uncategorizedKey ? '' : selectedKey.value
    await updatePersonalKnowledge(props.item.id, { directoryId })
    message.success(t('knowledge.moved_to',{name:selectedLabel.value}))
    emit('update:show', false)
    emit('moved')
  } catch (error: any) {
    message.error(error?.response?.data?.detail || t('knowledge.move_failed'))
  } finally {
    saving.value = false
  }
}

watch(() => props.show, value => {
  if (!value) return
  keyword.value = ''
  selectedKey.value = props.item?.directoryId || uncategorizedKey
  expandedKeys.value = props.item?.directoryId ? ancestorsOf(props.item.directoryId, props.directories) : []
})
</script>

<style scoped>
.intro{margin:0 0 14px;color:#667085;font-size:13px}.tree-frame{height:360px;margin-top:12px;padding:8px;overflow:hidden;border:1px solid #e5e9f1;border-radius:10px;background:#fbfcfe}.tree-frame :deep(.n-tree){height:100%}.tree-frame :deep(.n-tree-node-content){min-height:36px;border-radius:7px}.tree-frame :deep(.n-tree-node-content--selected){background:#eaf0ff}.tree-frame :deep(.n-empty){height:100%;display:grid;place-content:center}.target{display:flex;align-items:center;gap:10px;margin-top:12px;padding:10px 12px;border-radius:8px;background:#f5f7fb;color:#667085;font-size:12px}.target strong{min-width:0;overflow:hidden;color:#344054;text-overflow:ellipsis;white-space:nowrap}
</style>
