<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { NAlert, NButton, NDataTable, NGrid, NStatistic, NSpin, type DataTableColumns } from 'naive-ui'
import {
  listDevices,
  listKnowledgeDocuments,
  listModelInstances,
  listSkills,
  listTools,
  listUsers,
  queryAudit,
  fetchQuotaUsage,
  docId,
  docText,
  fmtTime,
  type AdminDoc,
  type QuotaUsage,
} from '../api/admin'
import { errorMessage, isPermissionDenied } from '../api/client'

/**
 * 概览页：把已有管理端列表端点聚合成计数卡片 + 最近审计。
 *
 * ## 为什么逐个数据源独立 catch
 *
 * 这些端点各自要不同权限码（user.manage / model.manage / skill.manage …），
 * 而 ZY 的角色是**按权限码放行**的——`ops` 只有 tool.manage + device.manage，
 * 用它登录时概览页有一半卡片注定 403。若用 `Promise.all` 整体 catch，
 * 一个 403 会让整页退化成错误提示，看不到"哪些资源是我能管的"这个有用信息。
 *
 * 所以：每个源单独 try，单个失败只在**自己那张卡片上**标注原因与所需权限码。
 * 这与 AdminShell 顶部那条"403 是后端鉴权结果，不是页面故障"是同一条口径。
 */

/** 一个计数卡片的数据源。`need` 用于在 403 时告诉用户缺哪个权限码。 */
interface Source {
  key: string
  label: string
  path: string
  endpoint: string
  need: string
  load: () => Promise<number>
}

const SOURCES: Source[] = [
  { key: 'users', label: '用户', path: '/users', endpoint: 'GET /admin/users', need: 'user.manage', load: async () => (await listUsers()).length },
  {
    key: 'models',
    label: '模型实例',
    path: '/model-instances',
    endpoint: 'GET /admin/model-instances',
    need: 'model.manage',
    load: async () => (await listModelInstances()).length,
  },
  { key: 'tools', label: '工具', path: '/tools', endpoint: 'GET /admin/tools', need: 'tool.manage', load: async () => (await listTools()).length },
  { key: 'skills', label: '技能', path: '/skills', endpoint: 'GET /admin/skills', need: 'skill.manage', load: async () => (await listSkills()).length },
  {
    key: 'documents',
    label: '知识文档',
    path: '/knowledge',
    endpoint: 'GET /admin/knowledge/documents',
    // 注意：knowledge_admin._guard 只认 admin 角色或 knowledge.manage，不认 knowledge.read
    // （member 恰好持有 knowledge.read，若写错会让用户产生"我有权限为什么 403"的困惑）
    need: 'admin 角色或 knowledge.manage',
    load: async () => (await listKnowledgeDocuments()).length,
  },
  { key: 'devices', label: '设备', path: '/devices', endpoint: 'GET /admin/devices', need: 'device.manage', load: async () => (await listDevices()).length },
]

/** 单个卡片的展示态。`value: null` 表示该源未取到（失败或未加载完）。 */
interface Panel {
  key: string
  label: string
  path: string
  endpoint: string
  need: string
  value: number | null
  error: string
  denied: boolean
}

const quota = ref<QuotaUsage | null>(null)
const quotaError = ref('')
const panels = ref<Panel[]>(
  SOURCES.map((s) => ({ key: s.key, label: s.label, path: s.path, endpoint: s.endpoint, need: s.need, value: null, error: '', denied: false })),
)
const audit = ref<{ items: AdminDoc[]; total: number }>({ items: [], total: 0 })
const auditError = ref('')
const loading = ref(false)

const totalTokens = () =>
  (quota.value?.model_tokens_prompt ?? 0) + (quota.value?.model_tokens_completion ?? 0)

function panelOf(key: string): Panel | undefined {
  return panels.value.find((p) => p.key === key)
}

async function loadQuota(): Promise<void> {
  try {
    quota.value = await fetchQuotaUsage()
    quotaError.value = ''
  } catch (err) {
    quota.value = null
    quotaError.value = isPermissionDenied(err)
      ? `${errorMessage(err)} —— 需要 quota.read 权限码`
      : errorMessage(err)
  }
}

async function loadPanel(source: Source): Promise<void> {
  const panel = panelOf(source.key)
  if (!panel) return
  try {
    panel.value = await source.load()
    panel.error = ''
    panel.denied = false
  } catch (err) {
    panel.value = null
    panel.denied = isPermissionDenied(err)
    panel.error = panel.denied ? `需要 ${source.need} 权限码` : errorMessage(err)
  }
}

async function loadAudit(): Promise<void> {
  try {
    audit.value = await queryAudit({ limit: 8 })
    auditError.value = ''
  } catch (err) {
    audit.value = { items: [], total: 0 }
    auditError.value = isPermissionDenied(err)
      ? `${errorMessage(err)} —— 需要 audit.read 权限码`
      : errorMessage(err)
  }
}

async function load(): Promise<void> {
  loading.value = true
  await Promise.all([loadQuota(), loadAudit(), ...SOURCES.map(loadPanel)])
  loading.value = false
}

/** Token 数量千分位：大数与文本混排时辨识成本高。 */
const fmtNumber = (n: number) => new Intl.NumberFormat('zh-CN').format(n)

const auditColumns: DataTableColumns<AdminDoc> = [
  // 字段口径对齐后端 audit_logs（governance/audit.py），与审计日志页共用同一份字段
  { title: '时间', key: 'occurred_at', width: 150, render: (row) => fmtTime(row.occurred_at) },
  { title: '操作者', key: 'actor_user_id', width: 150, render: (row) => docText(row, 'actor_user_id') },
  { title: '动作', key: 'action', width: 160, render: (row) => docText(row, 'action') },
  {
    title: '对象',
    key: 'target',
    render: (row) =>
      row.resource_type ? `${docText(row, 'resource_type')}:${docText(row, 'resource_id')}` : docText(row, 'resource_id'),
  },
]

onMounted(load)
</script>

<template>
  <div>
    <div class="flex items-center justify-between mb-3">
      <p class="muted text-[13px]">
        概览数字来自各管理端列表端点的当前条目数（不缓存、不预聚合）；某个源 403 只影响它自己那张卡片。
      </p>
      <n-button size="small" :loading="loading" @click="load">刷新</n-button>
    </div>

    <!-- 本月用量 -->
    <div class="panel">
      <h2 class="panel-title">本月用量</h2>
      <n-alert v-if="quotaError" type="warning" :bordered="false" style="margin-bottom: 12px">
        {{ quotaError }}
      </n-alert>
      <n-grid :cols="4" :x-gap="16" :y-gap="12" :cols-responsive="{ xs: 2, sm: 4 }">
        <n-grid-item>
          <n-statistic label="输入 Token" :value="fmtNumber(quota?.model_tokens_prompt ?? 0)" />
        </n-grid-item>
        <n-grid-item>
          <n-statistic label="输出 Token" :value="fmtNumber(quota?.model_tokens_completion ?? 0)" />
        </n-grid-item>
        <n-grid-item>
          <n-statistic label="合计 Token" :value="fmtNumber(totalTokens())" />
        </n-grid-item>
        <n-grid-item>
          <n-statistic label="工具调用" :value="fmtNumber(quota?.tool_calls ?? 0)" />
        </n-grid-item>
      </n-grid>
    </div>

    <!-- 资源计数 -->
    <div class="panel">
      <h2 class="panel-title">资源计数</h2>
      <n-grid :cols="3" :x-gap="16" :y-gap="12" :cols-responsive="{ xs: 2, sm: 3 }">
        <n-grid-item v-for="panel in panels" :key="panel.key">
          <div class="rounded-lg border border-slate-200 px-3.5 py-3 bg-white">
            <div class="text-[12px] text-slate-500 mb-1">{{ panel.label }}</div>
            <n-spin :show="loading">
              <div class="text-[22px] font-semibold text-slate-800 leading-7">
                {{ panel.value === null ? '—' : panel.value }}
              </div>
            </n-spin>
            <div v-if="panel.error" class="text-[11px] mt-1" :class="panel.denied ? 'text-amber-600' : 'text-red-600'">
              {{ panel.error }}
            </div>
            <div v-else class="text-[11px] mt-1">
              <router-link :to="panel.path" class="text-brand">前往管理 →</router-link>
            </div>
          </div>
        </n-grid-item>
      </n-grid>
    </div>

    <!-- 最近审计 -->
    <div class="panel">
      <h2 class="panel-title">最近审计</h2>
      <p class="panel-hint">
        取最近 8 条（共 {{ audit.total }} 条）。完整查询见
        <a class="text-brand" href="#/audit">审计日志</a> 页面。
      </p>
      <n-alert v-if="auditError" type="warning" :bordered="false" style="margin-bottom: 12px">
        {{ auditError }}
      </n-alert>
      <n-data-table
        v-else
        :columns="auditColumns"
        :data="audit.items"
        :row-key="(row: AdminDoc) => docId(row) || JSON.stringify(row)"
        size="small"
        :bordered="false"
        striped
        style="background: #fff"
      />
    </div>
  </div>
</template>