<template>
  <n-modal :show="show" preset="card" :title="t('skills.publish.title')" style="width:520px" @update:show="emit('update:show', $event)">
    <n-form label-placement="top">
      <n-form-item :label="t('skills.publish.version')"><n-input v-model:value="version" :placeholder="suggestedVersion" /></n-form-item>
      <n-form-item :label="t('skills.publish.notes')"><n-input v-model:value="notes" type="textarea" :rows="4" maxlength="2000" show-count /></n-form-item>
    </n-form>
    <template #footer><n-space justify="end"><n-button @click="emit('update:show', false)">{{ t('ui.cancel') }}</n-button><n-button type="primary" :loading="busy" @click="publish">{{ t('skills.publish.action') }}</n-button></n-space></template>
  </n-modal>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { NButton, NForm, NFormItem, NInput, NModal, NSpace, useMessage } from 'naive-ui'
import { publishSkill, type SkillItem } from '../../api/skills'
import { t } from '../../hooks/i18n'
const props = defineProps<{ show: boolean; skill: SkillItem | null }>()
const emit = defineEmits<{ 'update:show': [value: boolean]; published: [skill: SkillItem] }>()
const busy = ref(false), version = ref(''), notes = ref(''), message = useMessage()
const suggestedVersion = computed(() => props.skill?.lifecycle?.publishedVersion ? t('skills.publish.auto_patch') : '1.0.0')
watch(() => props.show, visible => { if (visible) { version.value = ''; notes.value = '' } })
async function publish() {
  if (!props.skill) return
  busy.value = true
  try { const result = await publishSkill(props.skill.id, version.value, notes.value); emit('published', result.skill); emit('update:show', false); message.success(t('skills.publish.success')) }
  catch (error: any) { message.error(error?.response?.data?.detail?.message || t('skills.publish.failed')) }
  finally { busy.value = false }
}
</script>
