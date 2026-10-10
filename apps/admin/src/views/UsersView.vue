<script setup lang="ts">
import { computed, h, onMounted, ref } from 'vue'
import { NButton, NTag, type DataTableColumns } from 'naive-ui'
import { listUsers, type AdminDoc } from '../api/admin'
import IdCell from '../components/IdCell.vue'
import ResourceTable from '../components/ResourceTable.vue'
import { useResourceList } from '../composables/useResourceList'

/** GET /api/v1/admin/users（需 user.manage）—— 后端只有读端点，故只读。 */
const loader = async (): Promise<AdminDoc[]> => {
  const users = await listUsers()
  return users.map((user) => ({
    id: user.id,
    username: user.username,
    display_name: user.display_name,
    status: user.status,
  }))
}

const { rows, loading, error, denied, reload } = useResourceList<AdminDoc>(loader)

const columns = computed<DataTableColumns<AdminDoc>>(() => [
  { title: '用户 ID', key: 'id', width: 120, render: (row) => h(IdCell, { id: String(row.id ?? '') }) },
  { title: '用户名', key: 'username', width: 180, render: (row) => String(row.username || '—') },
  { title: '显示名', key: 'display_name', render: (row) => String(row.display_name || '—') },
  {
    title: '状态',
    key: 'status',
    width: 110,
    render: (row) =>
      h(NTag, { size: 'small', bordered: false, type: row.status === 'active' ? 'success' : 'default' }, {
        default: () => String(row.status || '—'),
      }),
  },
])

onMounted(() => {
  void reload()
})
</script>

<template>
  <div>
    <div class="toolbar">
      <n-button size="small" @click="reload">刷新</n-button>
      <span v-if="rows.length" class="muted" style="font-size: 12px">共 {{ rows.length }} 人</span>
      <span class="muted" style="font-size: 12px">
        读端点 <code class="mono">GET /api/v1/admin/users</code>；后端暂无用户写端点，故本页只读
      </span>
    </div>
    <ResourceTable
      :columns="columns"
      :rows="rows"
      :loading="loading"
      :error="error"
      :denied="denied"
      empty-text="暂无用户"
      @refresh="reload"
    />
  </div>
</template>
