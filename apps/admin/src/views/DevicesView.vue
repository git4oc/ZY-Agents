<script setup lang="ts">
import { computed, h, onMounted, ref } from 'vue'
import { NButton, NPopconfirm, NTag, useMessage, type DataTableColumns } from 'naive-ui'
import { docText, listDevices, revokeDevice, type AdminDoc } from '../api/admin'
import { errorMessage } from '../api/client'
import IdCell from '../components/IdCell.vue'
import ResourceTable from '../components/ResourceTable.vue'
import { useResourceList } from '../composables/useResourceList'

/** GET /api/v1/admin/devices、DELETE /api/v1/admin/devices/{device_id}（需 device.manage）。 */
const { rows, loading, error, denied, reload } = useResourceList<AdminDoc>(listDevices)
const message = useMessage()
const revokingId = ref('')

function deviceIdOf(row: AdminDoc): string {
  // 后端按文档的 device_id 字段撤销（不是 _id）
  return String(row.device_id ?? '')
}

const columns = computed<DataTableColumns<AdminDoc>>(() => [
  { title: 'device_id', key: 'device_id', width: 160, render: (row) => h(IdCell, { id: deviceIdOf(row) }) },
  { title: '用户', key: 'user_id', width: 200, render: (row) => docText(row, 'user_id') },
  { title: '名称', key: 'name', render: (row) => docText(row, 'name') },
  { title: '平台', key: 'platform', width: 120, render: (row) => docText(row, 'platform') },
  {
    title: '状态',
    key: 'status',
    width: 150,
    render: (row) =>
      h(
        NTag,
        {
          size: 'small',
          bordered: false,
          type: row.status === 'revoked' ? 'error' : row.status === 'active' ? 'success' : 'default',
        },
        { default: () => String(row.status || '—') },
      ),
  },
  {
    title: '操作',
    key: 'ops',
    width: 110,
    render: (row) =>
      // 撤销立即使设备失效，属破坏性操作，与技能/知识库统一二次确认
      h(
        NPopconfirm,
        {
          positiveText: '撤销',
          negativeText: '取消',
          onPositiveClick: () => revoke(row),
        },
        {
          default: () => `确认撤销设备 ${deviceIdOf(row)}？撤销后该设备将立即失效。`,
          trigger: () =>
            h(
              NButton,
              {
                size: 'tiny',
                type: 'error',
                quaternary: true,
                disabled: !deviceIdOf(row) || row.status === 'revoked',
                loading: revokingId.value === deviceIdOf(row),
              },
              { default: () => '撤销' },
            ),
        },
      ),
  },
])

async function revoke(row: AdminDoc): Promise<void> {
  const deviceId = deviceIdOf(row)
  if (!deviceId) {
    message.error('该记录缺少 device_id 字段，无法调用撤销端点')
    return
  }
  revokingId.value = deviceId
  try {
    await revokeDevice(deviceId)
    message.success(`已撤销设备：${deviceId}`)
    await reload()
  } catch (err) {
    message.error(errorMessage(err, '撤销失败'))
  } finally {
    revokingId.value = ''
  }
}

onMounted(() => {
  void reload()
})
</script>

<template>
  <div>
    <div class="toolbar">
      <n-button size="small" @click="reload">刷新</n-button>
      <span v-if="rows.length" class="muted" style="font-size: 12px">共 {{ rows.length }} 台设备</span>
      <span class="muted" style="font-size: 12px">
        撤销对应 <code class="mono">DELETE /api/v1/admin/devices/{'{device_id}'}</code>（后端置为 revoked）
      </span>
    </div>

    <ResourceTable
      :columns="columns"
      :rows="rows"
      :loading="loading"
      :error="error"
      :denied="denied"
      empty-text="暂无设备"
      @refresh="reload"
    />
  </div>
</template>
