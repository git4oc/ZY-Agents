<template>
  <n-modal :show="show" preset="card" :title="t('knowledge.upload')" style="width:min(680px,calc(100vw - 32px))" @update:show="emit('update:show', $event)">
    <div class="dropzone" :class="{ dragging }" @dragenter.prevent="dragging=true" @dragover.prevent @dragleave.prevent="dragging=false" @drop.prevent="drop">
      <input ref="picker" type="file" multiple hidden @change="pick" />
      <strong>{{ t('knowledge.upload_drag') }}</strong><span>{{ t('knowledge.or') }}</span><n-button secondary @click="picker?.click()">{{ t('knowledge.choose_files') }}</n-button>
      <small>{{ t('knowledge.supported_files') }}</small>
    </div>
    <div v-if="files.length" class="files"><div v-for="(file,index) in files" :key="`${file.name}:${file.size}`"><span>{{ file.name }}</span><n-button text type="error" @click="files.splice(index,1)">{{ t('knowledge.remove') }}</n-button></div></div>
    <n-form-item :label="t('knowledge.tags')"><n-input v-model:value="tags" :placeholder="t('knowledge.tags_placeholder')" /></n-form-item>
    <n-progress v-if="uploading" type="line" :percentage="progress" />
    <template #footer><n-space justify="end"><n-button @click="emit('update:show',false)">{{ t('knowledge.cancel') }}</n-button><n-button type="primary" :disabled="!files.length" :loading="uploading" @click="submit">{{ t('knowledge.upload_and_learn') }}</n-button></n-space></template>
  </n-modal>
</template>
<script setup lang="ts">
import { ref } from 'vue'
import { NButton, NFormItem, NInput, NModal, NProgress, NSpace, useMessage } from 'naive-ui'
import { uploadPersonalKnowledge } from '../../api/personalKnowledge'
import { t } from '../../hooks/i18n'
const props = defineProps<{ show: boolean; directoryId: string }>()
const emit = defineEmits<{ 'update:show': [value: boolean]; uploaded: [] }>()
const message = useMessage(), picker = ref<HTMLInputElement | null>(null), files = ref<File[]>([]), tags = ref(''), dragging = ref(false), uploading = ref(false), progress = ref(0)
function add(list: FileList | null) { if (!list) return; files.value = Array.from(new Map([...files.value, ...Array.from(list)].map(file => [`${file.name}:${file.size}:${file.lastModified}`, file])).values()) }
function pick(event: Event) { add((event.target as HTMLInputElement).files); if (picker.value) picker.value.value = '' }
function drop(event: DragEvent) { dragging.value = false; add(event.dataTransfer?.files || null) }
async function submit() { uploading.value = true; progress.value = 0; try { const result = await uploadPersonalKnowledge(files.value, props.directoryId === 'all' ? '' : props.directoryId, tags.value, value => progress.value = value); const failed = result.items.filter(item => item.status === 'failed').length; message[failed ? 'warning' : 'success'](failed ? t('knowledge.upload_partial_failed',{count:failed}) : t('knowledge.upload_submitted')); files.value=[]; tags.value=''; emit('update:show',false); emit('uploaded') } catch (error:any) { message.error(error?.response?.data?.detail || error?.message || t('knowledge.upload_failed')) } finally { uploading.value=false } }
</script>
<style scoped>
.dropzone{min-height:190px;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:10px;border:2px dashed #ccd5e4;border-radius:14px;background:#fafcff;transition:.2s}.dropzone.dragging{border-color:#2459e8;background:#eef3ff}.dropzone strong{font-size:17px}.dropzone span,.dropzone small{color:#8993a4}.files{max-height:190px;margin:14px 0;overflow:auto}.files>div{display:flex;align-items:center;justify-content:space-between;padding:7px 10px;border-bottom:1px solid #eef1f5}
</style>
