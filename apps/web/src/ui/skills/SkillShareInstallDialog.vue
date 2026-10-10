<template>
  <n-modal :show="show" preset="card" :title="t('skills.share.install_title')" style="width: 560px" :mask-closable="false" @update:show="close">
    <n-spin :show="loading">
      <div v-if="preview" class="preview-content">
        <div class="preview-title">{{ preview.name }}</div>
        <div class="preview-description">{{ preview.description || t('skills.no_desc') }}</div>
        <n-space>
          <n-tag>{{ typeText(preview.type) }}</n-tag>
          <n-tag>{{ preview.version }}</n-tag>
          <n-tag>{{ t('skills.install_file_count', { count: preview.fileCount }) }}</n-tag>
        </n-space>
        <n-alert v-if="preview.alreadyInstalled" type="success" :title="t('skills.share.already_installed')" />
        <n-alert v-else-if="preview.hasConflict" type="warning" :title="t('skills.share.conflict_title')">
          {{ t('skills.share.conflict_hint') }}
        </n-alert>
        <p class="hint">{{ t('skills.share.install_hint') }}</p>
      </div>
      <n-alert v-else-if="errorMessage" type="error" :title="t('skills.share.unavailable')">{{ errorMessage }}</n-alert>
    </n-spin>
    <template #footer>
      <n-space justify="end">
        <n-button @click="close(false)">{{ t('ui.cancel') }}</n-button>
        <n-button v-if="!loggedIn" type="primary" @click="emit('login')">{{ t('skills.share.login_action') }}</n-button>
        <n-button
          v-if="preview && !preview.alreadyInstalled"
          type="primary"
          :loading="installing"
          @click="install"
        >{{ preview.hasConflict ? t('skills.share.replace') : t('skills.install_action') }}</n-button>
      </n-space>
    </template>
  </n-modal>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import { NAlert, NButton, NModal, NSpace, NSpin, NTag, useMessage } from 'naive-ui'
import type { SkillType } from '../../api/skills'
import { installSharedSkill, previewSkillShare, type SkillSharePreview } from '../../api/skillSharing'
import { t } from '../../hooks/i18n'
import { skillShareErrorMessage } from './skillShareErrors'

const props = defineProps<{ show: boolean; token: string; loggedIn: boolean }>()
const emit = defineEmits<{ close: []; installed: []; login: [] }>()
const message = useMessage()
const loading = ref(false)
const installing = ref(false)
const preview = ref<SkillSharePreview | null>(null)
const errorMessage = ref('')

watch(() => [props.show, props.token, props.loggedIn], () => {
  if (props.show && props.token) void load()
}, { immediate: true })

function typeText(type: SkillType) {
  if (type === 'expert_package') return t('skills.type.expert_package')
  if (type === 'workflow') return t('skills.type.workflow')
  if (type === 'writing_style') return t('skills.type.style')
  return t('skills.type.ordinary')
}

async function load() {
  preview.value = null
  if (!props.loggedIn) {
    errorMessage.value = t('skills.share.login_required')
    return
  }
  loading.value = true
  errorMessage.value = ''
  try {
    preview.value = await previewSkillShare(props.token)
  } catch (error: any) {
    errorMessage.value = skillShareErrorMessage(error, 'skills.share.unavailable_hint')
  } finally {
    loading.value = false
  }
}

async function install() {
  if (!preview.value) return
  installing.value = true
  try {
    await installSharedSkill(props.token, preview.value.hasConflict)
    message.success(t('skills.share.install_success'))
    emit('installed')
    close(false)
  } catch (error: any) {
    message.error(skillShareErrorMessage(error, 'skills.share.install_failed'))
  } finally {
    installing.value = false
  }
}

function close(value: boolean) {
  if (!value) emit('close')
}
</script>

<style scoped>
.preview-content { display: grid; gap: 14px; }
.preview-title { color: #101828; font-size: 22px; font-weight: 700; }
.preview-description, .hint { color: #667085; line-height: 1.65; }
.hint { margin: 0; font-size: 13px; }
</style>
