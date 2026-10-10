<template>
  <section class="preview-pane">
    <n-spin :show="loading">
      <PdfDocumentViewer
        v-if="previewUrl && previewKind === 'pdf'"
        :url="previewUrl"
        :title="title"
        :http-headers="previewHeaders"
      />
      <iframe v-else-if="objectUrl && previewKind === 'document'" :src="objectUrl" :title="title"></iframe>
      <div v-else-if="objectUrl" class="image-wrap"><img :src="objectUrl" :alt="title" /></div>
      <div v-else-if="!loading" class="preview-message">
        <n-empty :description="message" />
        <n-button v-if="documentId" size="small" secondary @click="load">{{ t('knowledge.reload') }}</n-button>
      </div>
    </n-spin>
  </section>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { NButton, NEmpty, NSpin } from 'naive-ui'
import { fetchKnowledgeSourceDocument, fetchKnowledgeSourcePreview, knowledgeSourceAuthHeaders, knowledgeSourcePreviewUrl } from '../../api/knowledgeSources'
import PdfDocumentViewer from './PdfDocumentViewer.vue'
import { t } from '../../hooks/i18n'

const props = defineProps<{ documentId: string; title: string }>()
const loading = ref(false)
const previewUrl = ref('')
const objectUrl = ref('')
const mimeType = ref('')
const message = ref(t('knowledge.no_preview'))
const previewHeaders = computed(() => knowledgeSourceAuthHeaders())
const previewKind = computed(() => {
  if (mimeType.value.includes('pdf')) return 'pdf'
  return mimeType.value.startsWith('image/') ? 'image' : 'document'
})

function clearPreview() {
  if (objectUrl.value) URL.revokeObjectURL(objectUrl.value)
  previewUrl.value = ''
  objectUrl.value = ''
}

async function load() {
  clearPreview()
  if (!props.documentId) {
    message.value = t('knowledge.not_uploaded')
    return
  }
  loading.value = true
  try {
    const document = await fetchKnowledgeSourceDocument(props.documentId)
    if (['queued', 'running', 'pending'].includes(document.previewStatus)) {
      message.value = t('knowledge.preview_generating')
      return
    }
    mimeType.value = document.previewMimeType || document.mimeType
    if (mimeType.value.includes('pdf')) {
      previewUrl.value = knowledgeSourcePreviewUrl(props.documentId)
    } else {
      const blob = await fetchKnowledgeSourcePreview(props.documentId)
      mimeType.value = blob.type || mimeType.value
      objectUrl.value = URL.createObjectURL(blob)
    }
  } catch (error: any) {
    const status = error?.message?.match(/Request failed: (\d+)/)?.[1]
    message.value = status === '409' ? t('knowledge.preview_pending') : status === '403' ? t('knowledge.preview_forbidden') : t('knowledge.preview_retry_failed')
  } finally {
    loading.value = false
  }
}

watch(() => props.documentId, load, { immediate: true })
onBeforeUnmount(clearPreview)
</script>

<style scoped>
.preview-pane{height:100%;min-height:0;overflow:hidden;border:1px solid #e5e9f1;border-radius:10px;background:#f7f9fc}.preview-pane :deep(.n-spin-container),.preview-pane :deep(.n-spin-content){height:100%}.preview-pane iframe{width:100%;height:100%;border:0;background:#fff}.image-wrap{display:grid;height:100%;place-items:center;overflow:auto;padding:18px}.image-wrap img{max-width:100%;max-height:100%;object-fit:contain}.preview-message{display:flex;height:100%;align-items:center;justify-content:center;flex-direction:column;gap:14px}
</style>
