<script setup lang="ts">
import { computed, h, onMounted, ref } from 'vue'
import {
  NButton,
  NForm,
  NFormItem,
  NInput,
  NSelect,
  NSpace,
  NTag,
  useMessage,
  type DataTableColumns,
  type FormInst,
  type FormRules,
} from 'naive-ui'
import { createTool, docId, docText, listTools, patchTool, type AdminDoc } from '../api/admin'
import { errorMessage } from '../api/client'
import IdCell from '../components/IdCell.vue'
import ResourceTable from '../components/ResourceTable.vue'
import { useResourceList } from '../composables/useResourceList'

/** GET/POST /api/v1/admin/tools、PATCH /api/v1/admin/tools/{tool_id}（需 tool.manage）。 */
const { rows, loading, error, denied, reload } = useResourceList<AdminDoc>(listTools)
const message = useMessage()

const creating = ref(false)
const patchingId = ref('')
const form = ref({ name: '', type: 'http', description: '', status: 'active' })
const formRef = ref<FormInst | null>(null)
const rules: FormRules = {
  name: [{ required: true, message: '工具名称不能为空', trigger: ['input', 'blur'] }],
}

const TYPE_OPTIONS = [
  { label: 'http', value: 'http' },
  { label: 'mcp', value: 'mcp' },
]
const STATUS_OPTIONS = [
  { label: 'active', value: 'active' },
  { label: 'disabled', value: 'disabled' },
]

const columns = computed<DataTableColumns<AdminDoc>>(() => [
  { title: 'ID', key: 'id', width: 120, render: (row) => h(IdCell, { id: docId(row) }) },
  { title: '名称', key: 'name', render: (row) => docText(row, 'name') },
  { title: '类型', key: 'type', width: 90, render: (row) => docText(row, 'type') },
  { title: '说明', key: 'description', render: (row) => docText(row, 'description') },
  {
    title: '状态',
    key: 'status',
    width: 130,
    render: (row) =>
      h(
        'div',
        { style: 'display:flex;align-items:center;gap:6px' },
        [
          h(
            NTag,
            { size: 'small', bordered: false, type: row.status === 'active' ? 'success' : 'default' },
            { default: () => String(row.status || '—') },
          ),
          h(
            NButton,
            {
              size: 'tiny',
              quaternary: true,
              loading: patchingId.value === docId(row),
              onClick: () => toggleStatus(row),
            },
            { default: () => (row.status === 'active' ? '停用' : '启用') },
          ),
        ],
      ),
  },
])

async function submit(): Promise<void> {
  try {
    await formRef.value?.validate()
  } catch {
    return
  }
  creating.value = true
  try {
    const result = await createTool({
      name: form.value.name.trim(),
      type: form.value.type,
      description: form.value.description.trim(),
      status: form.value.status,
    })
    message.success(`已创建工具：${result.id || '（后端未返回 id）'}`)
    form.value = { name: '', type: 'http', description: '', status: 'active' }
    await reload()
  } catch (err) {
    message.error(errorMessage(err, '创建失败'))
  } finally {
    creating.value = false
  }
}

async function toggleStatus(row: AdminDoc): Promise<void> {
  const id = docId(row)
  if (!id) {
    message.error('该记录缺少 _id，无法调用 PATCH')
    return
  }
  const nextStatus = row.status === 'active' ? 'disabled' : 'active'
  patchingId.value = id
  try {
    await patchTool(id, { status: nextStatus })
    message.success(`已${nextStatus === 'active' ? '启用' : '停用'}：${row.name ?? id}`)
    await reload()
  } catch (err) {
    message.error(errorMessage(err, '更新失败'))
  } finally {
    patchingId.value = ''
  }
}

onMounted(() => {
  void reload()
})
</script>

<template>
  <div>
    <div class="panel">
      <h2 class="panel-title">新建工具</h2>
      <p class="panel-hint">
        对应 <code class="mono">POST /api/v1/admin/tools</code>（需 tool.manage 角色码）。后端会把
        <code class="mono">org_unit_id</code> 强制取当前登录组织，状态开关走
        <code class="mono">PATCH /admin/tools/{'{tool_id}'}</code>。
      </p>
      <n-form
        ref="formRef"
        :model="form"
        :rules="rules"
        :disabled="creating"
        label-placement="left"
        style="max-width: 900px"
      >
        <n-form-item label="名称" path="name">
          <n-input v-model:value="form.name" placeholder="工具名" style="width: 200px" />
        </n-form-item>
        <n-form-item label="类型" path="type">
          <n-select v-model:value="form.type" :options="TYPE_OPTIONS" style="width: 130px" />
        </n-form-item>
        <n-form-item label="状态" path="status">
          <n-select v-model:value="form.status" :options="STATUS_OPTIONS" style="width: 140px" />
        </n-form-item>
        <n-form-item label="说明" path="description">
          <n-input v-model:value="form.description" placeholder="用途说明" style="width: 280px" />
        </n-form-item>
        <n-form-item>
          <n-space>
            <n-button type="primary" :loading="creating" @click="submit">创建</n-button>
            <n-button quaternary @click="reload">刷新列表</n-button>
          </n-space>
        </n-form-item>
      </n-form>
    </div>

    <ResourceTable
      :columns="columns"
      :rows="rows"
      :loading="loading"
      :error="error"
      :denied="denied"
      empty-text="暂无工具"
      @refresh="reload"
    />
  </div>
</template>
