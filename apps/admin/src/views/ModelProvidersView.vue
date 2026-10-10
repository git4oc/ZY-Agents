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
import {
  createModelProvider,
  docId,
  docText,
  listModelProviders,
  patchModelProvider,
  type AdminDoc,
} from '../api/admin'
import { errorMessage } from '../api/client'
import IdCell from '../components/IdCell.vue'
import ResourceTable from '../components/ResourceTable.vue'
import { useResourceList } from '../composables/useResourceList'

/**
 * 模型提供商：GET/POST /api/v1/admin/model-providers、PATCH /admin/model-providers/{id}
 * （需 model.manage 角色码）。
 *
 * 写端点每次成功都会触发 Profile 缓存失效（后端 `invalidate`），
 * 因此这里保存成功即意味着"下次编译会带上新配置"。
 */
const { rows, loading, error, denied, reload } = useResourceList<AdminDoc>(listModelProviders)
const message = useMessage()

const creating = ref(false)
const patchingId = ref('')
const form = ref({
  name: '',
  code: '',
  base_url: '',
  provider_type: 'openai_compatible',
  status: 'active',
})
const formRef = ref<FormInst | null>(null)
const rules: FormRules = {
  name: [{ required: true, message: '名称不能为空', trigger: ['input', 'blur'] }],
  code: [{ required: true, message: '编码不能为空', trigger: ['input', 'blur'] }],
}

const PROVIDER_TYPE_OPTIONS = [
  { label: 'openai_compatible', value: 'openai_compatible' },
  { label: 'azure_openai', value: 'azure_openai' },
  { label: 'anthropic', value: 'anthropic' },
  { label: 'dashscope', value: 'dashscope' },
  { label: 'local', value: 'local' },
]
const STATUS_OPTIONS = [
  { label: 'active', value: 'active' },
  { label: 'disabled', value: 'disabled' },
]

const columns = computed<DataTableColumns<AdminDoc>>(() => [
  { title: 'ID', key: 'id', width: 120, render: (row) => h(IdCell, { id: docId(row) }) },
  { title: '名称', key: 'name', render: (row) => docText(row, 'name') },
  { title: '编码', key: 'code', width: 150, render: (row) => docText(row, 'code') },
  { title: '类型', key: 'provider_type', width: 170, render: (row) => docText(row, 'provider_type') },
  { title: '基础地址', key: 'base_url', render: (row) => docText(row, 'base_url') },
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
    const result = await createModelProvider({
      name: form.value.name.trim(),
      code: form.value.code.trim(),
      base_url: form.value.base_url.trim(),
      provider_type: form.value.provider_type,
      status: form.value.status,
    })
    message.success(`已创建模型提供商：${result.id || '（后端未返回 id）'}`)
    form.value = {
      name: '',
      code: '',
      base_url: '',
      provider_type: 'openai_compatible',
      status: 'active',
    }
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
    await patchModelProvider(id, { status: nextStatus })
    message.success(`已${nextStatus === 'active' ? '启用' : '停用'}`)
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
      <h2 class="panel-title">新建模型提供商</h2>
      <p class="panel-hint">
        对应 <code class="mono">POST /api/v1/admin/model-providers</code>（需 model.manage 角色码）。
        后端有字段白名单：<code class="mono">api_key_enc</code> 等密钥字段不经写端点落库，
        故此处不提供对应输入框。保存成功会使 Profile 缓存失效，下次编译生效。
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
          <n-input v-model:value="form.name" placeholder="展示名，如 主模型服务" style="width: 220px" />
        </n-form-item>
        <n-form-item label="编码" path="code">
          <n-input v-model:value="form.code" placeholder="唯一编码，如 openai-main" style="width: 220px" />
        </n-form-item>
        <n-form-item label="类型" path="provider_type">
          <n-select v-model:value="form.provider_type" :options="PROVIDER_TYPE_OPTIONS" style="width: 220px" />
        </n-form-item>
        <n-form-item label="基础地址" path="base_url">
          <n-input
            v-model:value="form.base_url"
            placeholder="https://api.example.internal/v1"
            style="width: 360px"
          />
        </n-form-item>
        <n-form-item label="状态" path="status">
          <n-select v-model:value="form.status" :options="STATUS_OPTIONS" style="width: 140px" />
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
      empty-text="暂无模型提供商"
      @refresh="reload"
    />
  </div>
</template>
