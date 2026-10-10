<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { NAlert, NButton, NInput, NPagination, NSpace, NTag } from 'naive-ui'
import { docText, fmtTime, queryAudit, type AdminDoc } from '../api/admin'
import { errorMessage, isPermissionDenied } from '../api/client'

/** GET /api/v1/admin/audit?action=&limit=&offset=（需 audit.read）。 */
const rows = ref<AdminDoc[]>([])
const total = ref(0)
const loading = ref(false)
const error = ref('')
const denied = ref(false)
const action = ref('')
const offset = ref(0)
const limit = ref(50)

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  denied.value = false
  try {
    const result = await queryAudit({
      action: action.value.trim() || undefined,
      limit: limit.value,
      offset: offset.value,
    })
    rows.value = result.items
    total.value = result.total
  } catch (err) {
    rows.value = []
    total.value = 0
    denied.value = isPermissionDenied(err)
    error.value = denied.value
      ? `${errorMessage(err)} —— 审计查询需要 audit.read 角色码`
      : errorMessage(err)
  } finally {
    loading.value = false
  }
}

function search(): void {
  offset.value = 0
  void load()
}

onMounted(load)
</script>

<template>
  <div>
    <div class="toolbar">
      <n-input
        v-model:value="action"
        placeholder="按 action 过滤（留空为全部）"
        style="width: 240px"
        clearable
        @keyup.enter="search"
      />
      <n-button size="small" type="primary" :loading="loading" @click="search">查询</n-button>
      <n-button size="small" quaternary @click="load">刷新</n-button>
      <span class="muted" style="font-size: 12px">共 {{ total }} 条</span>
    </div>

    <div class="panel" style="padding: 0; overflow: hidden">
      <!-- 错误用 NAlert 承载（对齐全站错误模式）；缺权限码时重试必然失败，只给提示 -->
      <n-alert
        v-if="error"
        :type="denied ? 'warning' : 'error'"
        :bordered="false"
        style="padding: 14px 16px"
      >
        {{ error }}
        <template v-if="!denied" #action>
          <n-button size="tiny" quaternary @click="load">重试</n-button>
        </template>
      </n-alert>
      <table class="mono" style="width: 100%; border-collapse: collapse; font-size: 12px">
        <thead>
          <tr style="background: #fafafa; text-align: left">
            <th style="padding: 8px 12px; border-bottom: 1px solid var(--app-border)">时间</th>
            <th style="padding: 8px 12px; border-bottom: 1px solid var(--app-border)">动作</th>
            <th style="padding: 8px 12px; border-bottom: 1px solid var(--app-border)">主体</th>
            <th style="padding: 8px 12px; border-bottom: 1px solid var(--app-border)">资源</th>
            <th style="padding: 8px 12px; border-bottom: 1px solid var(--app-border)">详情</th>
          </tr>
        </thead>
        <tbody>
          <!-- 字段口径对齐后端 audit_logs（governance/audit.py）：occurred_at / actor_user_id / action / resource_type / resource_id / details -->
          <tr v-for="(row, index) in rows" :key="index" style="vertical-align: top">
            <td style="padding: 7px 12px; border-bottom: 1px solid var(--app-border)">
              {{ fmtTime(row.occurred_at) }}
            </td>
            <td style="padding: 7px 12px; border-bottom: 1px solid var(--app-border)">
              <n-tag size="small" :bordered="false" type="info">{{ String(row.action ?? '—') }}</n-tag>
            </td>
            <td style="padding: 7px 12px; border-bottom: 1px solid var(--app-border)">{{ docText(row, 'actor_user_id') }}</td>
            <td style="padding: 7px 12px; border-bottom: 1px solid var(--app-border)">
              {{ row.resource_type ? `${docText(row, 'resource_type')}:${docText(row, 'resource_id')}` : docText(row, 'resource_id') }}
            </td>
            <td style="padding: 7px 12px; border-bottom: 1px solid var(--app-border); word-break: break-all">
              {{ (() => {
                const detail = row.details
                if (detail === undefined || detail === null) return '—'
                const text = typeof detail === 'string' ? detail : JSON.stringify(detail)
                return text.length > 160 ? `${text.slice(0, 160)}…` : text
              })() }}
            </td>
          </tr>
          <tr v-if="!loading && !error && rows.length === 0">
            <td colspan="5" class="muted" style="padding: 24px; text-align: center">暂无审计记录</td>
          </tr>
        </tbody>
      </table>
    </div>

    <n-space style="margin-top: 12px; justify-content: flex-end">
      <n-pagination
        :page-size="limit"
        :item-count="total"
        :page="Math.floor(offset / limit) + 1"
        @update:page="(page: number) => { offset = (page - 1) * limit; load() }"
      />
    </n-space>
  </div>
</template>
