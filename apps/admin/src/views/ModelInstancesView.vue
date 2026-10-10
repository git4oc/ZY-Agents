<script setup lang="ts">
import { computed, h, onMounted, ref } from 'vue'
import {
  NButton,
  NDynamicTags,
  NForm,
  NFormItem,
  NInput,
  NInputNumber,
  NSelect,
  NSpace,
  NTag,
  useMessage,
  type DataTableColumns,
  type FormInst,
  type FormRules,
} from 'naive-ui'
import {
  createModelInstance,
  docId,
  docText,
  listModelInstances,
  listModelProviders,
  patchModelInstance,
  type AdminDoc,
} from '../api/admin'
import { errorMessage } from '../api/client'
import IdCell from '../components/IdCell.vue'
import ResourceTable from '../components/ResourceTable.vue'
import { useResourceList } from '../composables/useResourceList'

/**
 * 模型实例：GET/POST /api/v1/admin/model-instances、PATCH /admin/model-instances/{id}
 * （需 model.manage 角色码）。
 *
 * 写入即失效 Profile 缓存（后端 `invalidate`）——所以这里改完模型上下文窗口，
 * 下次编译就会带上新值。
 */
const { rows, loading, error, denied, reload } = useResourceList<AdminDoc>(listModelInstances)
const message = useMessage()

const creating = ref(false)
const patchingId = ref('')
const providerOptions = ref<{ label: string; value: string }[]>([])
const form = ref({
  provider_id: '',
  model_name: '',
  display_name: '',
  capabilities: ['chat'] as string[],
  max_context_tokens: 128000,
  status: 'active',
})

const STATUS_OPTIONS = [
  { label: 'active', value: 'active' },
  { label: 'disabled', value: 'disabled' },
]
const formRef = ref<FormInst | null>(null)
const rules: FormRules = {
  provider_id: [{ required: true, message: '请选择所属供应商', trigger: ['change', 'blur'] }],
  model_name: [{ required: true, message: '模型名不能为空', trigger: ['input', 'blur'] }],
}

const columns = computed<DataTableColumns<AdminDoc>>(() => [
  { title: 'ID', key: 'id', width: 120, render: (row) => h(IdCell, { id: docId(row) }) },
  { title: '名称', key: 'display_name', render: (row) => docText(row, 'display_name') },
  { title: '供应商', key: 'provider_id', width: 200, render: (row) => docText(row, 'provider_id') },
  { title: '模型', key: 'model_name', render: (row) => docText(row, 'model_name') },
  { title: '上下文', key: 'max_context_tokens', width: 110, render: (row) => docText(row, 'max_context_tokens') },
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

/** 供应商下拉：值是后端文档 _id（即实例的 provider_id）。 */
async function loadProviders(): Promise<void> {
  try {
    const providers = await listModelProviders()
    providerOptions.value = providers.map((p) => {
      const id = docId(p)
      const name = String(p.name ?? '')
      return { label: name ? `${name}（${id}）` : id, value: id }
    })
  } catch (err) {
    // 供应商列表拉不到不应阻断本页面（读权限可能与写权限不同）
    message.warning(`供应商下拉加载失败：${errorMessage(err)}`)
  }
}

async function submit(): Promise<void> {
  try {
    await formRef.value?.validate()
  } catch {
    return
  }
  creating.value = true
  try {
    const result = await createModelInstance({
      provider_id: form.value.provider_id,
      model_name: form.value.model_name.trim(),
      display_name: form.value.display_name.trim(),
      capabilities: form.value.capabilities,
      max_context_tokens: Number(form.value.max_context_tokens) || 0,
      status: form.value.status,
    })
    message.success(`已创建模型实例：${result.id || '（后端未返回 id）'}`)
    form.value = {
      provider_id: form.value.provider_id,
      model_name: '',
      display_name: '',
      capabilities: ['chat'],
      max_context_tokens: 128000,
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
    await patchModelInstance(id, { status: nextStatus })
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
  void loadProviders()
})
</script>

<template>
  <div>
    <div class="panel">
      <h2 class="panel-title">新建模型实例</h2>
      <p class="panel-hint">
        对应 <code class="mono">POST /api/v1/admin/model-instances</code>（需 model.manage 角色码）。
        字段对齐后端白名单；保存成功会使 Profile 缓存失效，下次编译生效。
      </p>
      <n-form
        ref="formRef"
        :model="form"
        :rules="rules"
        :disabled="creating"
        label-placement="left"
        style="max-width: 900px"
      >
        <n-form-item label="所属供应商" path="provider_id">
          <!-- 刻意不带 tag：手输任意 provider_id 会静默产生孤儿实例（后端无校验） -->
          <n-select
            v-model:value="form.provider_id"
            :options="providerOptions"
            filterable
            placeholder="选择供应商（列表为空时请先创建供应商）"
            style="width: 360px"
          />
        </n-form-item>
        <n-form-item label="模型名" path="model_name">
          <n-input v-model:value="form.model_name" placeholder="上游模型名，如 gpt-4o" style="width: 240px" />
        </n-form-item>
        <n-form-item label="展示名" path="display_name">
          <n-input v-model:value="form.display_name" placeholder="可选" style="width: 240px" />
        </n-form-item>
        <n-form-item label="能力" path="capabilities">
          <!-- NDynamicTags 替代逗号分隔文本：至少含 chat 的约束直接预置，不再靠 placeholder -->
          <div style="width: 100%">
            <n-dynamic-tags v-model:value="form.capabilities" />
            <div class="muted text-[12px]" style="margin-top: 4px">至少含 chat；输入后回车添加</div>
          </div>
        </n-form-item>
        <n-form-item label="上下文窗口" path="max_context_tokens">
          <n-input-number v-model:value="form.max_context_tokens" :min="0" style="width: 200px" />
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
      empty-text="暂无模型实例"
      @refresh="reload"
    />
  </div>
</template>
