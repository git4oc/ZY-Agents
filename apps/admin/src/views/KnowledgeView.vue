<script setup lang="ts">
import { computed, h, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { NAlert, NButton, NDataTable, NEmpty, NPopconfirm, NSpace, NSpin, NTag, useMessage, type DataTableColumns } from 'naive-ui'
import {
  deleteKnowledgeDocument,
  docText,
  listKnowledgeDocuments,
  uploadKnowledgeDocument,
  KNOWLEDGE_UPLOAD_EXTENSIONS,
  type KnowledgeDocument,
} from '../api/admin'
import { errorMessage, isPermissionDenied } from '../api/client'
import { useResourceList } from '../composables/useResourceList'

/**
 * 知识库管理（全 org 视角）。
 *
 * 对应端点（`services/api/app/api/endpoints/knowledge_admin.py`，前缀 `/api/v1/admin/knowledge`）：
 * - `GET /documents` —— 全 org 列表，含解析状态 / 分块数 / 错误信息（需 admin 或 knowledge.manage）
 * - `POST /documents` —— multipart 上传（`file` + `title`），**异步索引**
 * - `DELETE /documents/{id}` —— 原件 + chunks + 记录一并清理
 *
 * ## 为什么列表走 `/admin/knowledge/documents` 而不是 `/knowledge/documents`
 *
 * 用户端 `GET /knowledge/documents` 是**"我的文档"**视角（`view=mine` 按 `owner_user_id`
 * 过滤），管理员用它只能看到自己上传的。管理端要的是全 org，故必须走 admin 端点。
 *
 * ## 权限
 *
 * `knowledge_admin._guard` 是「**admin 角色直通，或显式 `knowledge.manage`**」——
 * 注意它**不看** `knowledge.read`。所以 `ops` 角色会 403，这是后端鉴权结果不是页面故障。
 */
const { rows, loading, error, denied, reload } = useResourceList<KnowledgeDocument>(
  listKnowledgeDocuments,
)
const router = useRouter()
const message = useMessage()

const uploading = ref(false)
const removingId = ref('')
const fileInput = ref<HTMLInputElement | null>(null)

/** 解析状态 → naive-ui tag type。未知状态统一走 default，不臆测语义。 */
function statusTag(status: string): { type: 'success' | 'warning' | 'error' | 'default'; text: string } {
  const s = status.toLowerCase()
  if (s === 'ready' || s === 'completed' || s === 'done') return { type: 'success', text: status || '—' }
  if (s === 'processing' || s === 'pending' || s === 'running') return { type: 'warning', text: status }
  if (s === 'failed' || s === 'error') return { type: 'error', text: status }
  return { type: 'default', text: status || '—' }
}

async function onPick(event: Event): Promise<void> {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  // 选中同一个文件两次也要能触发 change
  input.value = ''
  if (!file) return
  uploading.value = true
  try {
    const result = await uploadKnowledgeDocument(file)
    message.success(
      `已上传：${result.title || file.name}（id ${result.id || '未返回'}）—— 解析在后台进行，稍后刷新看状态`,
    )
    await reload()
  } catch (err) {
    message.error(errorMessage(err, '上传失败'))
  } finally {
    uploading.value = false
  }
}

async function remove(doc: KnowledgeDocument): Promise<void> {
  removingId.value = doc.id
  try {
    await deleteKnowledgeDocument(doc.id)
    message.success(`已删除：${doc.title || doc.id}`)
    await reload()
  } catch (err) {
    message.error(errorMessage(err, '删除失败'))
  } finally {
    removingId.value = ''
  }
}

const columns = computed<DataTableColumns<KnowledgeDocument>>(() => [
  {
    title: '标题',
    key: 'title',
    render: (row) =>
      h(
        'a',
        {
          href: `#/knowledge/${encodeURIComponent(row.id)}`,
          class: 'text-brand hover:underline',
          onClick: (e: MouseEvent) => {
            e.preventDefault()
            void router.push(`/knowledge/${encodeURIComponent(row.id)}`)
          },
        },
        row.title || row.original_filename || row.id,
      ),
  },
  { title: '原文件', key: 'original_filename', width: 220, render: (row) => row.original_filename || '—' },
  { title: '类型', key: 'file_ext', width: 80, render: (row) => row.file_ext || '—' },
  {
    title: '解析状态',
    key: 'status',
    width: 120,
    render: (row) => {
      const t = statusTag(row.status)
      return h(NTag, { size: 'small', bordered: false, type: t.type }, { default: () => t.text })
    },
  },
  { title: '分块', key: 'chunk_count', width: 80, render: (row) => row.chunk_count },
  {
    title: '错误',
    key: 'error',
    width: 220,
    render: (row) =>
      row.error
        ? h('span', { class: 'text-red-600 text-[12px]', title: row.error }, row.error)
        : h('span', { class: 'text-slate-400' }, '—'),
  },
  {
    title: '操作',
    key: 'actions',
    width: 140,
    render: (row) =>
      h(NSpace, { size: 2 }, {
        default: () => [
          h(
            NButton,
            {
              size: 'tiny',
              quaternary: true,
              onClick: () => void router.push(`/knowledge/${encodeURIComponent(row.id)}`),
            },
            { default: () => '详情' },
          ),
          // 删除会清掉原件 + 全部分块且不可恢复，必须二次确认（对齐 SkillsView 的模式）
          h(
            NPopconfirm,
            {
              positiveText: '删除',
              negativeText: '取消',
              onPositiveClick: () => void remove(row),
            },
            {
              default: () =>
                `确认删除《${row.title || row.original_filename || row.id}》？原件与全部分块将被清理，不可恢复。`,
              trigger: () =>
                h(
                  NButton,
                  {
                    size: 'tiny',
                    quaternary: true,
                    type: 'error',
                    loading: removingId.value === row.id,
                  },
                  { default: () => '删除' },
                ),
            },
          ),
        ],
      }),
  },
])

// 存在解析中的文档时，每 15s 自动刷一次状态，直到全部就绪/失败。
// 用 watch 而不是 onMounted 里的瞬时判断：rows 是异步加载的，判断必须在 reload 之后才有效。
let refreshTimer: ReturnType<typeof setTimeout> | undefined
watch(
  () => rows.value.some((r) => statusTag(r.status).type === 'warning'),
  (hasProcessing) => {
    if (refreshTimer !== undefined) {
      clearTimeout(refreshTimer)
      refreshTimer = undefined
    }
    if (hasProcessing) {
      refreshTimer = setTimeout(() => void reload(), 15000)
    }
  },
)

onMounted(() => {
  void reload()
})

onUnmounted(() => {
  if (refreshTimer !== undefined) clearTimeout(refreshTimer)
})
</script>

<template>
  <div>
    <div class="panel">
      <h2 class="panel-title">知识库文档</h2>
      <p class="panel-hint">
        对应 <code class="mono">GET /api/v1/admin/knowledge/documents</code>，返回全 org 文档
        （用户端 <code class="mono">/knowledge/documents</code> 是"我的文档"视角，管理员看不到别人的）。
        需要 <strong>admin 角色</strong>或 <code class="mono">knowledge.manage</code> 权限码。
      </p>
      <n-alert type="info" :bordered="false" style="margin: 10px 0">
        上传后解析在<strong>后台异步</strong>进行，列表先显示 <code class="mono">processing</code>，
        完成后变为就绪状态并带上分块数；解析失败会把原因写在「错误」列。
      </n-alert>

      <n-space align="center">
        <input
          ref="fileInput"
          type="file"
          class="hidden"
          :accept="KNOWLEDGE_UPLOAD_EXTENSIONS.join(',')"
          @change="onPick"
        />
        <n-button type="primary" :loading="uploading" @click="fileInput?.click()">上传文档</n-button>
        <span class="muted text-[12px]">
          支持 {{ KNOWLEDGE_UPLOAD_EXTENSIONS.join(' / ') }}（后端硬校验，其他类型直接 403）
        </span>
        <n-button quaternary :loading="loading" @click="reload">刷新</n-button>
      </n-space>
    </div>

    <n-alert v-if="error" :type="denied ? 'warning' : 'error'" :bordered="false" style="margin-bottom: 12px">
      {{ error }}
      <template #action>
        <n-button size="tiny" quaternary @click="reload">重试</n-button>
      </template>
    </n-alert>

    <n-spin :show="loading">
      <n-data-table
        v-if="rows.length > 0"
        :columns="columns"
        :data="rows"
        :row-key="(row: KnowledgeDocument) => row.id"
        size="small"
        :bordered="false"
        striped
        style="background: #fff"
      />
      <n-empty
        v-else-if="!loading && !error"
        description="暂无知识文档"
        style="padding: 40px 0; background: #fff"
      >
        <n-button type="primary" @click="fileInput?.click()">上传文档</n-button>
      </n-empty>
    </n-spin>
  </div>
</template>