<script setup lang="ts">
import { computed, h, onMounted, ref } from 'vue'
import {
  NAlert,
  NButton,
  NForm,
  NFormItem,
  NInput,
  NPopconfirm,
  NSelect,
  NSpace,
  NTag,
  useMessage,
  type DataTableColumns,
  type FormInst,
  type FormItemRule,
  type FormRules,
} from 'naive-ui'
import {
  createGrant,
  docId,
  docText,
  listGrants,
  listUsers,
  patchGrant,
  RESOURCE_TYPE_OPTIONS,
  type AdminDoc,
} from '../api/admin'
import { errorMessage } from '../api/client'
import IdCell from '../components/IdCell.vue'
import ResourceTable from '../components/ResourceTable.vue'
import { useResourceList } from '../composables/useResourceList'

/**
 * 资源授权：GET/POST /api/v1/admin/grants、PATCH /admin/grants/{id}（需 acl.manage 角色码）。
 *
 * ⚠️ **deny 优先**（后端 `identity/acl.py` 判定）：只要存在任意一条命中该主体的
 * `deny`，即使同时存在 `allow` 也会被拒绝。这在私有化环境里是刻意的——"临时收回某人
 * 某个模型的权限"必须立刻生效，不能因为还有一条 allow 就被放过。
 */
const { rows, loading, error, denied, reload } = useResourceList<AdminDoc>(listGrants)
const message = useMessage()

const creating = ref(false)
const patchingId = ref('')
const form = ref({
  resource_type: 'tool',
  resource_id: '',
  subject_type: 'all',
  subject_id: '',
  effect: 'allow',
})

const SUBJECT_TYPE_OPTIONS = [
  { label: 'all（全体）', value: 'all' },
  { label: 'user（指定用户）', value: 'user' },
  { label: 'role（指定角色）', value: 'role' },
]
const EFFECT_OPTIONS = [
  { label: 'allow（允许）', value: 'allow' },
  { label: 'deny（拒绝，优先级更高）', value: 'deny' },
]

// ---------------- 表单校验 ----------------

/** Mongo ObjectId 形态校验：填错 ID 只会在授权判定时静默失效，必须在入口拦住。 */
const OID_RE = /^[0-9a-fA-F]{24}$/
const formRef = ref<FormInst | null>(null)
const rules: FormRules = {
  resource_id: [
    { required: true, message: '资源 ID 不能为空', trigger: ['input', 'blur'] },
    {
      validator: (_rule: FormItemRule, value: string) =>
        !value || !value.trim() || !value.includes(' ')
          ? true
          : new Error('资源 ID 不应包含空格'),
      trigger: ['input', 'blur'],
    },
  ],
  subject_id: [
    {
      validator: (_rule: FormItemRule, value: string) => {
        // subject_type=all 时后端存 null，不填合法
        if (form.value.subject_type === 'all') return true
        if (!value || !value.trim()) return new Error('主体类型不是 all 时，主体 ID 不能为空')
        if (!OID_RE.test(value.trim())) return new Error('应为 24 位十六进制 ObjectId')
        return true
      },
      trigger: ['input', 'blur'],
    },
  ],
}

// ---------------- 主体选择器（用户）----------------

/**
 * 用户下拉数据源。role-bindings 与 grants 都要 user.manage，所以这里一般拉得到；
 * 拉不到时退回手填输入框，不阻断授权。
 */
const userOptions = ref<{ label: string; value: string }[]>([])
async function loadUserOptions(): Promise<void> {
  try {
    userOptions.value = (await listUsers()).map((u) => ({
      label: u.display_name ? `${u.display_name}（${u.username}）` : u.username,
      value: u.id,
    }))
  } catch {
    // 读权限不足时静默退回手填，不弹全局提示
  }
}

const columns = computed<DataTableColumns<AdminDoc>>(() => [
  { title: 'ID', key: 'id', width: 120, render: (row) => h(IdCell, { id: docId(row) }) },
  { title: '资源类型', key: 'resource_type', width: 150, render: (row) => docText(row, 'resource_type') },
  { title: '资源 ID', key: 'resource_id', render: (row) => docText(row, 'resource_id') },
  { title: '主体类型', key: 'subject_type', width: 110, render: (row) => docText(row, 'subject_type') },
  { title: '主体 ID', key: 'subject_id', render: (row) => docText(row, 'subject_id') },
  {
    title: '效果',
    key: 'effect',
    width: 150,
    render: (row) =>
      h(
        NTag,
        {
          size: 'small',
          bordered: false,
          type: row.effect === 'deny' ? 'error' : 'success',
        },
        { default: () => String(row.effect || '—') },
      ),
  },
  {
    title: '操作',
    key: 'actions',
    width: 110,
    render: (row) =>
      // deny 立即生效且优先于 allow（尤其 subject_type=all 时会切断全员访问），必须二次确认
      h(
        NPopconfirm,
        {
          positiveText: '确认',
          negativeText: '取消',
          onPositiveClick: () => flipEffect(row),
        },
        {
          default: () => flipConfirmText(row),
          trigger: () =>
            h(
              NButton,
              {
                size: 'tiny',
                quaternary: true,
                loading: patchingId.value === docId(row),
              },
              { default: () => (row.effect === 'deny' ? '改为 allow' : '改为 deny') },
            ),
        },
      ),
  },
])

/** deny 会立即生效且优先于 allow，翻转前必须说明影响面。 */
function flipConfirmText(row: AdminDoc): string {
  const next = row.effect === 'deny' ? 'allow' : 'deny'
  if (next === 'deny') {
    const scope = String(row.subject_type) === 'all' ? '全体用户（all）' : `主体 ${docText(row, 'subject_id')}`
    return `${scope}将立即失去对该资源的访问（deny 优先于 allow），确认改为 deny？`
  }
  return '确认恢复为 allow？'
}

async function submit(): Promise<void> {
  // 字段级校验：必填与 ObjectId 形态都在这里统一拦截，错误内联在表单项下方
  try {
    await formRef.value?.validate()
  } catch {
    return
  }
  creating.value = true
  try {
    const result = await createGrant({
      resource_type: form.value.resource_type,
      resource_id: form.value.resource_id.trim(),
      subject_type: form.value.subject_type as 'user' | 'role' | 'all',
      // subject_type=all 时后端存 null，故不传 subject_id
      subject_id: form.value.subject_type === 'all' ? null : form.value.subject_id.trim(),
      effect: form.value.effect as 'allow' | 'deny',
    })
    message.success(
      result.id
        ? `已创建${form.value.effect === 'deny' ? '拒绝' : '允许'}授权：${result.id}`
        : '已创建授权（后端未返回 id）',
    )
    form.value = {
      resource_type: 'tool',
      resource_id: '',
      subject_type: 'all',
      subject_id: '',
      effect: 'allow',
    }
    await reload()
  } catch (err) {
    message.error(errorMessage(err, '创建失败'))
  } finally {
    creating.value = false
  }
}

/** 翻转 allow/deny（PATCH 的真实用法：收回权限）。 */
async function flipEffect(row: AdminDoc): Promise<void> {
  const id = docId(row)
  if (!id) {
    message.error('该记录缺少 _id，无法调用 PATCH')
    return
  }
  const next = row.effect === 'deny' ? 'allow' : 'deny'
  patchingId.value = id
  try {
    await patchGrant(id, { effect: next })
    message.success(`已改为 ${next}`)
    await reload()
  } catch (err) {
    message.error(errorMessage(err, '更新失败'))
  } finally {
    patchingId.value = ''
  }
}

onMounted(() => {
  void reload()
  void loadUserOptions()
})
</script>

<template>
  <div>
    <n-alert type="warning" :bordered="false" style="margin-bottom: 14px">
      <strong>deny 优先</strong>：只要存在任意一条命中该主体的 <code class="mono">deny</code>，
      即使同时存在 <code class="mono">allow</code> 也会被拒绝。这样"临时收回某人某资源的权限"
      才能立刻生效，不会因为还有一条 allow 就被放过。
    </n-alert>

    <div class="panel">
      <h2 class="panel-title">新建资源授权</h2>
      <p class="panel-hint">
        对应 <code class="mono">POST /api/v1/admin/grants</code>（需 acl.manage 角色码）。
        授权变化会影响 Profile 编译输入，保存成功后后端会失效 Profile 缓存。
        <code class="mono">effect</code> 只接受 <code class="mono">allow</code> /
        <code class="mono">deny</code>，其它值后端返回 422。
      </p>
      <n-form
        ref="formRef"
        :model="form"
        :rules="rules"
        :disabled="creating"
        label-placement="left"
        style="max-width: 900px"
      >
        <n-form-item label="资源类型" path="resource_type">
          <n-select v-model:value="form.resource_type" :options="RESOURCE_TYPE_OPTIONS" style="width: 260px" />
        </n-form-item>
        <n-form-item label="资源 ID" path="resource_id">
          <n-input v-model:value="form.resource_id" placeholder="具体资源 id（可在下方列表复制）" style="width: 320px" />
        </n-form-item>
        <n-form-item label="主体类型" path="subject_type">
          <n-select v-model:value="form.subject_type" :options="SUBJECT_TYPE_OPTIONS" style="width: 260px" />
        </n-form-item>
        <n-form-item v-if="form.subject_type !== 'all'" label="主体 ID" path="subject_id">
          <n-select
            v-if="userOptions.length > 0"
            v-model:value="form.subject_id"
            :options="userOptions"
            filterable
            tag
            placeholder="搜索用户，或直接粘贴 24 位 ID"
            style="width: 320px"
          />
          <n-input v-else v-model:value="form.subject_id" placeholder="用户 _id 或角色 _id" style="width: 320px" />
        </n-form-item>
        <n-form-item label="效果" path="effect">
          <n-select v-model:value="form.effect" :options="EFFECT_OPTIONS" style="width: 260px" />
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
      empty-text="暂无资源授权"
      @refresh="reload"
    />
  </div>
</template>
