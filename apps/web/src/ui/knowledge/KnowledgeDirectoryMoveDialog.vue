<template>
  <n-modal
    :show="show"
    preset="card"
    :title="t('knowledge.move_directory')"
    style="width:min(520px,calc(100vw - 32px))"
    @update:show="emit('update:show', $event)"
  >
    <n-select v-model:value="parentId" :options="options" :placeholder="t('knowledge.select_parent_directory')" />
    <template #footer>
      <n-space justify="end">
        <n-button @click="emit('update:show', false)">{{ t('knowledge.cancel') }}</n-button>
        <n-button type="primary" :loading="saving" @click="submit">{{ t('knowledge.move') }}</n-button>
      </n-space>
    </template>
  </n-modal>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { NButton, NModal, NSelect, NSpace, useMessage } from 'naive-ui'
import { updateKnowledgeDirectory, type KnowledgeDirectory } from '../../api/personalKnowledge'
import { t } from '../../hooks/i18n'

const props = defineProps<{ show: boolean; item: KnowledgeDirectory | null; directories: KnowledgeDirectory[] }>()
const emit = defineEmits<{ 'update:show': [value: boolean]; moved: [] }>()
const message = useMessage()
const parentId = ref('')
const saving = ref(false)

const options = computed(() => {
  const excluded = new Set<string>()
  const collect = (node: KnowledgeDirectory) => {
    excluded.add(node.id)
    ;(node.children || []).forEach(collect)
  }
  if (props.item) collect(props.item)
  const rows: Array<{ label: string; value: string }> = [{ label: t('knowledge.root_directory'), value: '' }]
  const walk = (items: KnowledgeDirectory[], depth: number) => items.forEach(item => {
    if (!excluded.has(item.id)) rows.push({ label: `${'　'.repeat(depth)}${item.name}`, value: item.id })
    walk(item.children || [], depth + 1)
  })
  walk(props.directories, 0)
  return rows
})

async function submit() {
  if (!props.item) return
  saving.value = true
  try {
    await updateKnowledgeDirectory(props.item.id, props.item.name, parentId.value)
    message.success(t('knowledge.directory_moved'))
    emit('update:show', false)
    emit('moved')
  } catch (error: any) {
    message.error(error?.response?.data?.detail || t('knowledge.directory_move_failed'))
  } finally {
    saving.value = false
  }
}

watch(() => props.show, value => {
  if (value) parentId.value = props.item?.parentId || ''
})
</script>
