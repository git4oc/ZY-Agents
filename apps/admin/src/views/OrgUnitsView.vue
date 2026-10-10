<script setup lang="ts">
import { computed, h, onMounted, ref, watch } from 'vue'
import { NButton, NForm, NFormItem, NInput, NSelect, NSpace, NTag, useMessage, type DataTableColumns, type FormInst, type FormRules } from 'naive-ui'
import { createOrgUnit, docId, docText, listOrgUnits, type AdminDoc } from '../api/admin'
import { errorMessage } from '../api/client'
import IdCell from '../components/IdCell.vue'
import ResourceTable from '../components/ResourceTable.vue'
import { useResourceList } from '../composables/useResourceList'
import {
  buildOrgUnitHierarchy,
  orgUnitNameById,
  suggestCodePrefix,
  suggestOrgUnitCode,
} from '../orgUnits'

/**
 * 组织单元管理。
 *
 * 对应 `GET/POST /api/v1/admin/org-units`（需 org.manage）。
 *
 * ## 编码为什么自动生成
 *
 * `org_units` 唯一索引 `org_units_code_u = {code: 1}` 是**全局唯一**，而后端
 * `OrgUnitsRepository.create` **不做重名预检**——撞码时抛的是 Mongo 重复键异常，
 * 使用者看不懂。所以编码在前端按规则生成（`{前缀}-{序号}`），仍可手动覆盖。
 * 规则见 `src/orgUnits.ts`。
 *
 * ## 父级为什么改成下拉
 *
 * 原来这里是**手填 ObjectId**——使用者得先去列表里把 24 位 id 复制过来，
 * 且填错只会静默产生一棵孤儿节点（后端不校验 parent_id 是否存在）。
 * 现在按层级列出可选父级，根节点用缩进表达层级，孤儿/环形数据会被标出来。
 */
const { rows, loading, error, denied, reload } = useResourceList<AdminDoc>(listOrgUnits)
const message = useMessage()

const creating = ref(false)
const form = ref({ name: '', prefix: '', code: '', parent_id: null as string | null })
/** 用户手改过前缀后就不再跟随名称自动重算，避免把用户输入覆盖掉。 */
const prefixTouched = ref(false)
/** 用户手改过编码后，名称变化同样不再自动覆盖（此前只记了前缀的 touched，会把手改的编码冲掉）。 */
const codeTouched = ref(false)
const formRef = ref<FormInst | null>(null)
const rules: FormRules = {
  name: [{ required: true, message: '名称不能为空', trigger: ['input', 'blur'] }],
}

const existingCodes = computed(() => rows.value.map((row) => String(row.code ?? '')).filter(Boolean))

function regenerateCode(): void {
  const prefix = prefixTouched.value ? form.value.prefix.trim() : suggestCodePrefix(form.value.name)
  form.value.prefix = prefix
  form.value.code = suggestOrgUnitCode(form.value.name, existingCodes.value, prefix)
  codeTouched.value = false
}

/** 用户一改前缀就锁定它，之后名称变化不再自动覆盖前缀与编码。 */
function onPrefixInput(): void {
  prefixTouched.value = true
}

/** 用户一改编码就锁定它（n-input 只在用户输入时发 update:value，程序赋值不会误触发）。 */
function onCodeInput(): void {
  codeTouched.value = true
}

/** 放弃手动编码，恢复跟随名称自动生成。 */
function restoreAutoCode(): void {
  codeTouched.value = false
  regenerateCode()
}

watch(
  () => form.value.name,
  () => {
    if (!prefixTouched.value && !codeTouched.value) regenerateCode()
  },
)

/**
 * 父级选项：按 DFS 前序排列并用全角空格缩进，直观表达层级。
 * 末项的「层级异常」来自 `buildOrgUnitHierarchy` 对孤儿/环形的兜底标注。
 */
const parentOptions = computed(() =>
  buildOrgUnitHierarchy(rows.value).map((option) => ({
    label: `${'　'.repeat(option.depth)}${option.depth > 0 ? '└ ' : ''}${option.label}`,
    value: option.value,
  })),
)

const columns = computed<DataTableColumns<AdminDoc>>(() => [
  { title: 'ID', key: 'id', width: 120, render: (row) => h(IdCell, { id: docId(row) }) },
  { title: '名称', key: 'name', render: (row) => docText(row, 'name') },
  {
    title: '编码',
    key: 'code',
    width: 180,
    render: (row) => h(NTag, { size: 'small', bordered: false }, { default: () => docText(row, 'code') }),
  },
  {
    // 展示父级**名称**而不是裸 ObjectId：24 位 id 对使用者没有信息量
    title: '父级',
    key: 'parent_id',
    width: 240,
    render: (row) => {
      const name = orgUnitNameById(rows.value, row.parent_id)
      return name === '—' ? h('span', { class: 'text-slate-400' }, '（顶级）') : name
    },
  },
])

async function submit(): Promise<void> {
  // 字段级校验：错误内联在表单项下方，不再依赖一闪而过的 toast
  try {
    await formRef.value?.validate()
  } catch {
    return
  }
  // 编码允许留空后自动补：点过"重新生成"或输入过名称就已有值，这里兜底防空编码
  if (!form.value.code.trim()) regenerateCode()
  creating.value = true
  try {
    const result = await createOrgUnit({
      name: form.value.name.trim(),
      code: form.value.code.trim(),
      parent_id: form.value.parent_id || null,
    })
    message.success(`已创建组织单元：${result.id || '（后端未返回 id）'}`)
    form.value = { name: '', prefix: '', code: '', parent_id: null }
    prefixTouched.value = false
    codeTouched.value = false
    await reload()
  } catch (err) {
    message.error(errorMessage(err, '创建失败（编码重复时后端会报唯一索引冲突）'))
  } finally {
    creating.value = false
  }
}

onMounted(() => {
  void reload()
})
</script>

<template>
  <div>
    <div class="panel">
      <h2 class="panel-title">新建组织单元</h2>
      <p class="panel-hint">
        对应 <code class="mono">POST /api/v1/admin/org-units</code>（需 org.manage 角色码）。
        编码按 <code class="mono">&#123;前缀&#125;-&#123;序号&#125;</code> 自动生成且全局唯一
        （后端唯一索引 <code class="mono">org_units_code_u</code>），可手动覆盖。
        创建后暂不支持修改/删除，提交前请确认编码与父级。
      </p>
      <n-form
        ref="formRef"
        :model="form"
        :rules="rules"
        :disabled="creating"
        label-placement="left"
        style="max-width: 860px"
      >
        <n-form-item label="名称" path="name">
          <n-input
            v-model:value="form.name"
            placeholder="如 华东研发中心"
            style="width: 220px"
          />
        </n-form-item>
        <n-form-item label="编码前缀" path="prefix">
          <n-input
            v-model:value="form.prefix"
            placeholder="自动取名称中的字母/数字，如 east-rd"
            style="width: 180px"
            @update:value="onPrefixInput"
          />
        </n-form-item>
        <n-form-item label="编码" path="code">
          <n-space align="center">
            <n-input v-model:value="form.code" placeholder="自动生成，可改" style="width: 180px" @update:value="onCodeInput" />
            <n-button size="small" quaternary :disabled="creating" @click="regenerateCode">
              重新生成
            </n-button>
            <n-button v-if="codeTouched" size="small" quaternary :disabled="creating" @click="restoreAutoCode">
              恢复自动
            </n-button>
            <span class="text-[12px]" :class="codeTouched ? 'text-amber-600' : 'text-slate-400'">
              {{ codeTouched ? '已手动修改，改名不再自动更新' : '跟随名称自动生成，可手动覆盖' }}
            </span>
          </n-space>
        </n-form-item>
        <n-form-item label="父级" path="parent_id">
          <n-select
            v-model:value="form.parent_id"
            :options="parentOptions"
            filterable
            clearable
            placeholder="不选则为顶级组织单元"
            style="width: 320px"
          />
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
      empty-text="暂无组织单元——在上方表单创建第一个（编码会自动生成）"
      @refresh="reload"
    />
  </div>
</template>