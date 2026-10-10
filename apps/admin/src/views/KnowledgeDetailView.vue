<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NAlert, NButton, NDescriptions, NDescriptionsItem, NSpin } from 'naive-ui'
import { fetchKnowledgePreview, fetchKnowledgeSourceMeta, type KnowledgeSourceMeta } from '../api/admin'
import { errorMessage } from '../api/client'

/**
 * 知识文档详情（管理端视角）。
 *
 * ## 为什么元信息走 `/sources/documents/{id}` 而不是 `/knowledge/documents/{id}`
 *
 * - `_load_document`（sources）只做 **org 隔离** → 管理端能看全 org 任何人的文档
 * - `_load_visible`（用户端详情）要 **owner 或 active grant** → 管理端看别人的文档会 404
 *
 * 代价是拿不到用户端独有的 `shareCount` / `accessStatus`，以及**共享管理**
 * （`/knowledge/documents/{id}/grants` 同样是 owner 视角）。这两项目前后端都没有
 * 管理端等价端点，页面里如实标注"当前后端未提供"，不假装可用。
 *
 * 分块浏览同理：后端只有 `GET /sources/documents/{id}/chunks/{chunk_id}`（按 id 取单个），
 * **没有"列出该文档全部分块"的端点**，所以这里只显示 `chunkCount`，不伪造分块列表。
 */
const route = useRoute()
const router = useRouter()

const meta = ref<KnowledgeSourceMeta | null>(null)
const loading = ref(false)
const error = ref('')

const previewLoading = ref(false)
const previewError = ref('')
/** 文本类预览直接渲染成字符串；二进制类走 blob URL 交给 <iframe>。 */
const previewText = ref('')
const previewUrl = ref('')
const previewContentType = ref('')

const documentId = () => String(route.params.id ?? '')

function revokePreviewUrl(): void {
  if (previewUrl.value) {
    URL.revokeObjectURL(previewUrl.value)
    previewUrl.value = ''
  }
}

async function loadMeta(): Promise<void> {
  const id = documentId()
  if (!id) return
  loading.value = true
  error.value = ''
  meta.value = null
  try {
    meta.value = await fetchKnowledgeSourceMeta(id)
  } catch (err) {
    error.value = errorMessage(err, '读取文档信息失败')
  } finally {
    loading.value = false
  }
}

async function loadPreview(): Promise<void> {
  const id = documentId()
  if (!id) return
  previewLoading.value = true
  previewError.value = ''
  previewText.value = ''
  revokePreviewUrl()
  try {
    const { blob, contentType } = await fetchKnowledgePreview(id)
    previewContentType.value = contentType
    if (contentType.startsWith('text/')) {
      previewText.value = await blob.text()
    } else {
      previewUrl.value = URL.createObjectURL(blob)
    }
  } catch (err) {
    previewError.value = errorMessage(err, '预览加载失败')
  } finally {
    previewLoading.value = false
  }
}

watch(documentId, () => {
  void loadMeta()
})

onMounted(() => {
  void loadMeta()
})

onBeforeUnmount(revokePreviewUrl)
</script>

<template>
  <div>
    <div class="flex items-center gap-2 mb-3">
      <n-button size="small" quaternary @click="router.push('/knowledge')">← 返回列表</n-button>
      <n-button size="small" :loading="loading" @click="loadMeta">刷新</n-button>
    </div>

    <n-alert v-if="error" type="error" :bordered="false" style="margin-bottom: 12px">
      {{ error }}
    </n-alert>

    <n-spin :show="loading">
      <div v-if="meta">
        <div class="panel">
          <h2 class="panel-title">{{ meta.name || meta.originalFilename || meta.id }}</h2>
          <n-descriptions label-placement="left" bordered size="small" :column="2" style="margin-top: 10px">
            <n-descriptions-item label="ID">
              <span class="mono text-[12px]">{{ meta.id }}</span>
            </n-descriptions-item>
            <n-descriptions-item label="作用域">{{ meta.scope || '—' }}</n-descriptions-item>
            <n-descriptions-item label="原文件">{{ meta.originalFilename || '—' }}</n-descriptions-item>
            <n-descriptions-item label="类型">
              {{ meta.fileExt || '—' }} / {{ meta.mimeType || '—' }}
            </n-descriptions-item>
            <n-descriptions-item label="分块数">{{ meta.chunkCount }}</n-descriptions-item>
            <n-descriptions-item label="预览状态">{{ meta.previewStatus || '—' }}</n-descriptions-item>
          </n-descriptions>

          <n-alert type="info" :bordered="false" style="margin-top: 12px">
            <div class="text-[12px] leading-5">
              <div>
                · <strong>分块浏览</strong>：后端只提供「按 chunk_id 取单个分块」，**没有列出整篇分块的端点**，
                因此这里只显示分块总数，不提供逐块浏览。
              </div>
              <div>
                · <strong>共享管理</strong>：<code class="mono">/knowledge/documents/&#123;id&#125;/grants</code>
                是 owner 视角（校验文档归属），管理端当前**无法**查看或回收他人文档的共享。
              </div>
              <div>
                · 若这两项要上管理台，需要后端在 <code class="mono">knowledge_admin</code> 补等价端点。
              </div>
            </div>
          </n-alert>
        </div>

        <div class="panel">
          <div class="flex items-center justify-between mb-2">
            <h2 class="panel-title" style="margin: 0">内容预览</h2>
            <n-button size="small" :loading="previewLoading" @click="loadPreview">加载预览</n-button>
          </div>

          <n-alert v-if="previewError" type="warning" :bordered="false">{{ previewError }}</n-alert>

          <n-spin :show="previewLoading">
            <pre
              v-if="previewText"
              class="text-[12px] leading-5 bg-slate-50 border border-slate-200 rounded-md p-3 max-h-[520px] overflow-auto whitespace-pre-wrap break-all"
            >{{ previewText }}</pre>

            <iframe
              v-else-if="previewUrl"
              :src="previewUrl"
              class="w-full border border-slate-200 rounded-md"
              style="height: 520px"
              title="文档预览"
            ></iframe>

            <div
              v-else-if="!previewError && !previewLoading"
              class="rounded-md border border-slate-200 bg-slate-50 text-center text-slate-400 text-[13px] py-10"
            >
              点「加载预览」拉取内容（{{ meta.previewMimeType || '未知类型' }}）
            </div>
          </n-spin>
        </div>
      </div>
    </n-spin>
  </div>
</template>