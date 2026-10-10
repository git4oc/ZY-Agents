<template>
  <n-form label-placement="top" class="metadata-form">
    <n-form-item :label="t('knowledge.name')"><n-input v-model:value="name" maxlength="180" show-count /></n-form-item>
    <n-form-item :label="t('knowledge.description')"><n-input v-model:value="description" type="textarea" :autosize="{ minRows: 3, maxRows: 7 }" maxlength="2000" show-count /></n-form-item>
    <n-form-item :label="t('knowledge.tags')"><n-dynamic-tags v-model:value="tags" :max="20" /></n-form-item>
    <n-button type="primary" :loading="saving" :disabled="!name.trim()" @click="save">{{ t('knowledge.save_info') }}</n-button>
    <p class="hint">{{ t('knowledge.info_hint') }}</p>
  </n-form>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import { NButton, NDynamicTags, NForm, NFormItem, NInput, useMessage } from 'naive-ui'
import { updatePersonalKnowledge, type PersonalKnowledge } from '../../api/personalKnowledge'
import { t } from '../../hooks/i18n'

const props = defineProps<{ knowledge: PersonalKnowledge }>()
const emit = defineEmits<{ saved: [] }>()
const message = useMessage()
const name = ref('')
const description = ref('')
const tags = ref<string[]>([])
const saving = ref(false)

function reset() {
  name.value = props.knowledge.name
  description.value = props.knowledge.description || ''
  tags.value = [...(props.knowledge.tags || [])]
}

async function save() {
  if (!name.value.trim()) return
  saving.value = true
  try {
    await updatePersonalKnowledge(props.knowledge.id, {
      name: name.value.trim(),
      description: description.value.trim(),
      tags: tags.value.map(item => item.trim()).filter(Boolean),
    })
    message.success(t('knowledge.info_updated'))
    emit('saved')
  } catch (error: any) {
    message.error(error?.response?.data?.detail || t('knowledge.save_failed'))
  } finally {
    saving.value = false
  }
}

watch(() => props.knowledge, reset, { immediate: true })
</script>

<style scoped>
.metadata-form{max-width:680px}.hint{margin-top:12px;color:#8a94a5;font-size:12px}
</style>
