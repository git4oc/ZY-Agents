<script setup lang="ts">
import { NAlert, NButton, NDataTable, NEmpty, NSpace, NSpin, type DataTableColumns } from 'naive-ui'
import type { AdminDoc } from '../api/admin'

/** 只读资源表格：错误如实展示（不填充假数据），空列表显示空态。 */
defineProps<{
  columns: DataTableColumns<AdminDoc>
  rows: AdminDoc[]
  loading: boolean
  error: string
  denied: boolean
  emptyText?: string
}>()

const emit = defineEmits<{ (event: 'refresh'): void }>()
</script>

<template>
  <div>
    <n-alert v-if="error" :type="denied ? 'warning' : 'error'" :bordered="false" style="margin-bottom: 12px">
      <n-space align="center" :size="12">
        <span>{{ error }}</span>
        <!-- 缺权限码时重试必然失败，不渲染重试按钮（避免给用户错误预期） -->
        <n-button v-if="!denied" size="tiny" quaternary @click="emit('refresh')">重试</n-button>
      </n-space>
    </n-alert>

    <n-spin :show="loading">
      <n-data-table
        :columns="columns"
        :data="rows"
        :row-key="(row: AdminDoc) => String(row._id ?? row.id ?? '')"
        size="small"
        :bordered="false"
        striped
        style="background: #fff"
      />
      <n-empty
        v-if="!loading && !error && rows.length === 0"
        :description="emptyText || '暂无数据'"
        style="padding: 28px 0; background: #fff"
      />
    </n-spin>
  </div>
</template>
