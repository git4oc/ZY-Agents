<script setup lang="ts">
import { computed, h, onMounted, ref } from 'vue'
import {
  NCollapse,
  NCollapseItem,
  NForm,
  NFormItem,
  NInput,
  NSelect,
  NButton,
  NSpace,
  useMessage,
  type DataTableColumns,
  type FormInst,
  type FormItemRule,
  type FormRules,
} from 'naive-ui'
import {
  createRoleBinding,
  docId,
  docText,
  listRoleBindings,
  listUsers,
  patchRoleBinding,
  RESOURCE_TYPE_OPTIONS,
  type AdminDoc,
} from '../api/admin'
import { errorMessage } from '../api/client'
import IdCell from '../components/IdCell.vue'
import ResourceTable from '../components/ResourceTable.vue'
import { useResourceList } from '../composables/useResourceList'

/**
 * 角色绑定：GET/POST /api/v1/admin/role-bindings、PATCH /admin/role-bindings/{id}
 * （需 user.manage 角色码）。
 *
 * 角色绑定决定用户可见的模型 / 工具 / 技能集合 → 影响 Profile 编译输入，
 * 所以后端每次写入都会失效 Profile 缓存。
 */
const { rows, loading, error, denied, reload } = useResourceList<AdminDoc>(listRoleBindings)
const message = useMessage()

const creating = ref(false)
const patchingId = ref('')
const form = ref({
  subject_type: 'user',
  subject_id: '',
  role_code: '',
  scope: 'org_unit',
  resource_type: '',
  resource_id: '',
})

const SUBJECT_TYPE_OPTIONS = [
  { label: 'user（用户）', value: 'user' },
  { label: 'role（角色，用于资源授权）', value: 'role' },
]
const SCOPE_OPTIONS = [
  { label: 'org_unit（整个组织单元）', value: 'org_unit' },
  { label: 'resource（限定到某个资源）', value: 'resource' },
]

// ---------------- 角色码下拉 ----------------

/**
 * 角色码下拉：种子角色 + 现有绑定中出现过的角色码。原来是自由文本，
 * 手输错别字（如 admn）会静默创建一条永不生效的绑定——这是授权旅程最大的防错点。
 */
const roleCodeOptions = computed(() => {
  const codes = new Set<string>(['admin', 'ops', 'member'])
  for (const row of rows.value) {
    const code = docText(row, 'role_code')
    if (code && code !== '—') codes.add(code)
  }
  return [...codes].map((c) => ({ label: c, value: c }))
})

// ---------------- 主体选择器（用户）----------------

/** 用户下拉数据源；拉不到（无 user.manage）时退回手填输入框，不阻断创建。 */
const userOptions = ref<{ label: string; value: string }[]>([])
async function loadUserOptions(): Promise<void> {
  try {
    userOptions.value = (await listUsers()).map((u) => ({
      label: u.display_name ? `${u.display_name}（${u.username}）` : u.username,
      value: u.id,
    }))
  } catch {
    // 读权限不足时静默退回手填
  }
}

// ---------------- 表单校验 ----------------

const OID_RE = /^[0-9a-fA-F]{24}$/
const formRef = ref<FormInst | null>(null)
const rules: FormRules = {
  subject_id: [
    {
      validator: (_rule: FormItemRule, value: string) => {
        if (!value || !value.trim()) return new Error('主体 ID 不能为空')
        if (!OID_RE.test(value.trim())) return new Error('应为 24 位十六进制 ObjectId')
        return true
      },
      trigger: ['input', 'blur'],
    },
  ],
  role_code: [{ required: true, message: '角色码不能为空', trigger: ['change', 'blur'] }],
  resource_type: [
    {
      validator: (_rule: FormItemRule, value: string) =>
        form.value.scope !== 'resource' || (value && value.trim())
          ? true
          : new Error('范围限定到资源时，资源类型不能为空'),
      trigger: ['change', 'blur'],
    },
  ],
  resource_id: [
    {
      validator: (_rule: FormItemRule, value: string) => {
        if (form.value.scope !== 'resource') return true
        if (!value || !value.trim()) return new Error('范围限定到资源时，资源 ID 不能为空')
        if (!OID_RE.test(value.trim())) return new Error('应为 24 位十六进制 ObjectId')
        return true
      },
      trigger: ['input', 'blur'],
    },
  ],
}

const columns = computed<DataTableColumns<AdminDoc>>(() => [
  { title: 'ID', key: 'id', width: 120, render: (row) => h(IdCell, { id: docId(row) }) },
  {
    title: '主体类型',
    key: 'subject_type',
    width: 110,
    render: (row) => docText(row, 'subject_type'),
  },
  { title: '主体 ID', key: 'subject_id', render: (row) => docText(row, 'subject_id') },
  { title: '角色', key: 'role_code', width: 180, render: (row) => docText(row, 'role_code') },
  { title: '范围', key: 'scope', width: 120, render: (row) => docText(row, 'scope') },
  {
    title: '限定资源',
    key: 'resource',
    width: 220,
    render: (row) =>
      row.scope === 'resource'
        ? `${docText(row, 'resource_type')}:${docText(row, 'resource_id')}`
        : '—',
  },
  {
    title: '操作',
    key: 'actions',
    width: 90,
    render: (row) =>
      h(
        NButton,
        {
          size: 'tiny',
          quaternary: true,
          loading: patchingId.value === docId(row),
          onClick: () => clearResourceScope(row),
        },
        { default: () => '收回' },
      ),
  },
])

async function submit(): Promise<void> {
  // 字段级校验：必填与 ObjectId 形态都在这里统一拦截，错误内联在表单项下方
  try {
    await formRef.value?.validate()
  } catch {
    return
  }
  creating.value = true
  try {
    const result = await createRoleBinding({
      subject_type: form.value.subject_type as 'user' | 'role',
      subject_id: form.value.subject_id.trim(),
      role_code: form.value.role_code.trim(),
      scope: form.value.scope as 'org_unit' | 'resource',
      resource_type: form.value.scope === 'resource' ? form.value.resource_type.trim() : null,
      resource_id: form.value.scope === 'resource' ? form.value.resource_id.trim() : null,
    })
    message.success(`已创建角色绑定：${result.id || '（后端未返回 id）'}`)
    form.value = {
      subject_type: 'user',
      subject_id: '',
      role_code: '',
      scope: 'org_unit',
      resource_type: '',
      resource_id: '',
    }
    await reload()
  } catch (err) {
    message.error(errorMessage(err, '创建失败'))
  } finally {
    creating.value = false
  }
}

/** 把资源级绑定收回为组织单元级（PATCH 的真实用法演示）。 */
async function clearResourceScope(row: AdminDoc): Promise<void> {
  const id = docId(row)
  if (!id) {
    message.error('该记录缺少 _id，无法调用 PATCH')
    return
  }
  patchingId.value = id
  try {
    await patchRoleBinding(id, {
      scope: 'org_unit',
      resource_type: null,
      resource_id: null,
    })
    message.success('已收回为组织单元级授权')
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
    <n-collapse style="margin-bottom: 14px">
      <n-collapse-item title="角色 → 权限码速查（种子角色，以服务端 roles 集合实际配置为准）" name="roles">
        <div class="text-[13px] leading-6">
          <div><strong>admin</strong> —— org / user / model / tool / skill / device / audit / quota / acl / brand 共 10 项</div>
          <div><strong>ops</strong> —— tool.manage + device.manage（资源与运维类页面可用）</div>
          <div><strong>member</strong> —— knowledge.read（仅知识库读取，进管理端会大面积 403）</div>
          <div class="muted text-[12px] mt-1">403 提示中出现的权限码即按此对照授角色；自定义角色需在服务端 roles 集合里另行配置。</div>
        </div>
      </n-collapse-item>
    </n-collapse>

    <div class="panel">
      <h2 class="panel-title">新建角色绑定</h2>
      <p class="panel-hint">
        对应 <code class="mono">POST /api/v1/admin/role-bindings</code>（需 user.manage 角色码）。
        角色绑定决定该用户可见的模型 / 工具 / 技能集合，因此保存成功会使 Profile 缓存失效。
      </p>
      <n-form
        ref="formRef"
        :model="form"
        :rules="rules"
        :disabled="creating"
        label-placement="left"
        style="max-width: 900px"
      >
        <n-form-item label="主体类型" path="subject_type">
          <n-select v-model:value="form.subject_type" :options="SUBJECT_TYPE_OPTIONS" style="width: 280px" />
        </n-form-item>
        <n-form-item label="主体 ID" path="subject_id">
          <n-select
            v-if="form.subject_type === 'user' && userOptions.length > 0"
            v-model:value="form.subject_id"
            :options="userOptions"
            filterable
            tag
            placeholder="搜索用户，或直接粘贴 24 位 ID"
            style="width: 280px"
          />
          <n-input v-else v-model:value="form.subject_id" placeholder="用户 _id 或角色 _id" style="width: 280px" />
        </n-form-item>
        <n-form-item label="角色码" path="role_code">
          <n-select
            v-model:value="form.role_code"
            :options="roleCodeOptions"
            filterable
            placeholder="选择角色码（来自种子角色与现有绑定）"
            style="width: 240px"
          />
        </n-form-item>
        <n-form-item label="范围" path="scope">
          <n-select v-model:value="form.scope" :options="SCOPE_OPTIONS" style="width: 280px" />
        </n-form-item>
        <template v-if="form.scope === 'resource'">
          <n-form-item label="资源类型" path="resource_type">
            <n-select
              v-model:value="form.resource_type"
              :options="RESOURCE_TYPE_OPTIONS"
              placeholder="选择资源类型"
              style="width: 240px"
            />
          </n-form-item>
          <n-form-item label="资源 ID" path="resource_id">
            <n-input v-model:value="form.resource_id" placeholder="具体资源 id（24 位 ObjectId）" style="width: 280px" />
          </n-form-item>
        </template>
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
      empty-text="暂无角色绑定"
      @refresh="reload"
    />
  </div>
</template>
