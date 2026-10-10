<script setup lang="ts">
import { computed, h, onMounted, ref } from 'vue'
import {
  NAlert,
  NButton,
  NDescriptions,
  NDescriptionsItem,
  NDrawer,
  NDrawerContent,
  NForm,
  NFormItem,
  NInput,
  NPopconfirm,
  NSelect,
  NSpace,
  NSwitch,
  NTag,
  useDialog,
  useMessage,
  type DataTableColumns,
  type FormInst,
  type FormRules,
} from 'naive-ui'
import { createSkill, deleteSkill, docId, docText, fetchSkill, fmtTime, listSkills, patchSkill, type AdminDoc, } from '../api/admin'
import { errorMessage } from '../api/client'
import IdCell from '../components/IdCell.vue'
import ResourceTable from '../components/ResourceTable.vue'
import { useResourceList } from '../composables/useResourceList'

/**
 * 技能管理：列表 + 新建 + 详情编辑。
 *
 * 对应端点（`services/api/app/api/endpoints/admin.py`）：
 * - `GET /admin/skills` —— 读取放宽为登录用户
 * - `GET /admin/skills/{id}` —— 详情，**同样放宽**（无 `skill.manage` 守卫）
 * - `POST /admin/skills` —— 需 `skill.manage`
 * - `PATCH /admin/skills/{id}` —— 需 `skill.manage`，可写 name/description/scenario/type/config/enabled
 * - `DELETE /admin/skills/{id}` —— 需 `skill.manage`
 *
 * ⚠️ 开关字段是 `enabled`，不是 `status`：`enabled` ↔ mongo `status` 的双向映射
 * 由后端 `_skill_jsonable` / `patch_skill` 负责，前端不碰 `status`。
 * 后端写完还会 `invalidate(org_unit_id)` 让 Profile 缓存失效，下一轮即生效。
 */
const { rows, loading, error, denied, reload } = useResourceList<AdminDoc>(listSkills)
const message = useMessage()

const creating = ref(false)
// ★ key 是唯一索引 skills_org_key_u = (org_unit_id, key) 的组成部分，后端必填（缺失 422）
const form = ref({ key: '', name: '', description: '', status: 'active' })
const formRef = ref<FormInst | null>(null)
const rules: FormRules = {
  key: [{ required: true, message: '技能 key 不能为空（唯一索引字段，后端必填）', trigger: ['input', 'blur'] }],
  name: [{ required: true, message: '技能名称不能为空', trigger: ['input', 'blur'] }],
}

const STATUS_OPTIONS = [
  { label: 'active', value: 'active' },
  { label: 'disabled', value: 'disabled' },
]

const TYPE_OPTIONS = [
  { label: 'ordinary', value: 'ordinary' },
  { label: 'workflow', value: 'workflow' },
  { label: 'plugin', value: 'plugin' },
]

// ---------------- 详情抽屉 ----------------

const drawerOpen = ref(false)
const detailLoading = ref(false)
const saving = ref(false)
const removing = ref(false)
const detailError = ref('')
/** `key` 是唯一索引字段，只读展示——改它等于换一条技能，后端也没开放写入。 */
const detail = ref<Record<string, string | boolean> | null>(null)
/** config 后端是 dict，前端用 JSON 文本编辑，保存前 parse 并校验必须是对象。 */
const configText = ref('{}')
const edit = ref({ name: '', description: '', scenario: '', type: 'ordinary', enabled: true })

function fillFromDetail(d: Record<string, string | boolean>): void {
  detail.value = d
  edit.value = {
    name: String(d.name ?? ''),
    description: String(d.description ?? ''),
    scenario: String(d.scenario ?? ''),
    type: String(d.type ?? 'ordinary'),
    enabled: d.enabled === true,
  }
}

async function openDetail(skillId: string): Promise<void> {
  drawerOpen.value = true
  detailLoading.value = true
  detailError.value = ''
  detail.value = null
  try {
    const d = await fetchSkill(skillId)
    configText.value = JSON.stringify(d.config ?? {}, null, 2)
    fillFromDetail(d as unknown as Record<string, string | boolean>)
    // 加载完成后重置脏状态基线
    configError.value = ''
    baseline.value = drawerSnapshot()
  } catch (err) {
    detail.value = null
    detailError.value = errorMessage(err, '读取技能详情失败')
  } finally {
    detailLoading.value = false
  }
}

function onDrawerShow(show: boolean): void {
  if (!show) requestClose()
}

// ---------------- 抽屉脏数据保护 ----------------

const dialog = useDialog()
/** 加载完成后记录基线；编辑过任意字段（含 config）即视为脏。 */
const baseline = ref('')
function drawerSnapshot(): string {
  return JSON.stringify({ edit: edit.value, config: configText.value })
}
const dirty = computed(() => baseline.value !== '' && drawerSnapshot() !== baseline.value)

function closeDrawer(): void {
  drawerOpen.value = false
  detail.value = null
  detailError.value = ''
  baseline.value = ''
}

/** 关闭前检查脏状态：有未保存修改时弹确认，而不是静默丢弃。 */
function requestClose(): void {
  if (!dirty.value) {
    closeDrawer()
    return
  }
  dialog.warning({
    title: '放弃未保存的修改？',
    content: '详情抽屉里有未保存的修改，关闭后将丢失。',
    positiveText: '放弃修改并关闭',
    negativeText: '继续编辑',
    onPositiveClick: () => closeDrawer(),
  })
}

/** 静默解析 config 文本；非法 JSON / 非对象返回 {error}，供保存、失焦校验、格式化三处复用。 */
function parseConfigText(): { config: Record<string, unknown> } | { error: string } {
  const text = configText.value.trim() || '{}'
  let parsed: unknown
  try {
    parsed = JSON.parse(text)
  } catch (err) {
    return { error: `config 不是合法 JSON：${errorMessage(err, '解析失败')}` }
  }
  if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
    return { error: 'config 必须是 JSON 对象（如 {}），不能是数组或标量' }
  }
  return { config: parsed as Record<string, unknown> }
}

/** config 失焦/保存时的内联错误，呈现在 config 输入框下方。 */
const configError = ref('')

function parseConfig(): Record<string, unknown> | null {
  const result = parseConfigText()
  if ('error' in result) {
    message.warning(result.error)
    configError.value = result.error
    return null
  }
  configError.value = ''
  return result.config
}

/** 失焦时预校验：300 行 JSON 里找语法错误前就能看到出错原因。 */
function validateConfigOnBlur(): void {
  const result = parseConfigText()
  configError.value = 'error' in result ? result.error : ''
}

/** 一键格式化 config（错误时只标错误不覆盖用户输入）。 */
function formatConfig(): void {
  const result = parseConfigText()
  if ('error' in result) {
    configError.value = result.error
    return
  }
  configError.value = ''
  configText.value = JSON.stringify(result.config, null, 2)
}

async function save(): Promise<void> {
  const id = detail.value?.id
  if (typeof id !== 'string' || !id) return
  const config = parseConfig()
  if (config === null) return
  saving.value = true
  try {
    await patchSkill(id, {
      name: edit.value.name.trim(),
      description: edit.value.description,
      scenario: edit.value.scenario,
      type: edit.value.type,
      enabled: edit.value.enabled,
      config,
    })
    message.success('已保存（Profile 缓存已失效，下一轮生效）')
    await reload()
    await openDetail(id)
  } catch (err) {
    message.error(errorMessage(err, '保存失败'))
  } finally {
    saving.value = false
  }
}

async function remove(skillId: string): Promise<void> {
  removing.value = true
  try {
    await deleteSkill(skillId)
    message.success(`已删除技能：${skillId}`)
    drawerOpen.value = false
    await reload()
  } catch (err) {
    message.error(errorMessage(err, '删除失败'))
  } finally {
    removing.value = false
  }
}

const columns = computed<DataTableColumns<AdminDoc>>(() => [
  { title: 'ID', key: 'id', width: 120, render: (row) => h(IdCell, { id: docId(row) }) },
  { title: 'Key', key: 'key', width: 180, render: (row) => docText(row, 'key') },
  { title: '名称', key: 'name', render: (row) => docText(row, 'name') },
  { title: '说明', key: 'description', render: (row) => docText(row, 'description') },
  {
    title: '状态',
    key: 'status',
    width: 110,
    render: (row) =>
      h(
        NTag,
        { size: 'small', bordered: false, type: row.status === 'active' ? 'success' : 'default' },
        { default: () => String(row.status || '—') },
      ),
  },
  { title: '创建时间', key: 'created_at', width: 150, render: (row) => fmtTime(docText(row, 'created_at')) },
  {
    title: '操作',
    key: 'actions',
    width: 130,
    render: (row) => {
      const id = docId(row)
      return h(NSpace, { size: 2 }, {
        default: () => [
          h(
            NButton,
            { size: 'tiny', quaternary: true, disabled: !id, onClick: () => void openDetail(id) },
            { default: () => '详情' },
          ),
          h(
            NPopconfirm,
            {
              disabled: !id,
              positiveText: '删除',
              negativeText: '取消',
              onPositiveClick: () => void remove(id),
            },
            {
              default: () => '删除后不可恢复，确认？',
              trigger: () =>
                h(
                  NButton,
                  { size: 'tiny', quaternary: true, type: 'error', disabled: !id || removing.value },
                  { default: () => '删除' },
                ),
            },
          ),
        ],
      })
    },
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
    const result = await createSkill({
      key: form.value.key.trim(),
      name: form.value.name.trim(),
      description: form.value.description.trim(),
      status: form.value.status,
    })
    message.success(`已创建技能：${result.id || '（后端未返回 id）'}`)
    form.value = { key: '', name: '', description: '', status: 'active' }
    await reload()
  } catch (err) {
    message.error(errorMessage(err, '创建失败'))
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
      <h2 class="panel-title">新建技能</h2>
      <p class="panel-hint">
        对应 <code class="mono">POST /api/v1/admin/skills</code>（需 skill.manage 角色码）。
        <code class="mono">key</code> 是唯一索引 <code class="mono">(org_unit_id, key)</code> 的组成部分，创建后**不可改**。
      </p>
      <n-form
        ref="formRef"
        :model="form"
        :rules="rules"
        :disabled="creating"
        label-placement="left"
        style="max-width: 860px"
      >
        <n-form-item label="Key" path="key">
          <n-input
            v-model:value="form.key"
            placeholder="稳定标识，如 report_writer（唯一，必填）"
            style="width: 260px"
          />
        </n-form-item>
        <n-form-item label="名称" path="name">
          <n-input v-model:value="form.name" placeholder="技能名" style="width: 200px" />
        </n-form-item>
        <n-form-item label="状态" path="status">
          <n-select v-model:value="form.status" :options="STATUS_OPTIONS" style="width: 140px" />
        </n-form-item>
        <n-form-item label="说明" path="description">
          <n-input v-model:value="form.description" placeholder="用途说明" style="width: 300px" />
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
      empty-text="暂无技能"
      @refresh="reload"
    />

    <!-- 详情 / 编辑抽屉 -->
    <n-drawer :show="drawerOpen" :width="560" @update:show="onDrawerShow">
      <n-drawer-content title="技能详情" closable>
        <n-alert v-if="detailError" type="error" :bordered="false" style="margin-bottom: 12px">
          {{ detailError }}
        </n-alert>

        <n-spin :show="detailLoading">
          <div v-if="detail">
            <n-descriptions
              label-placement="left"
              bordered
              size="small"
              :column="1"
              style="margin-bottom: 16px"
            >
              <n-descriptions-item label="Key">{{ detail.key }}</n-descriptions-item>
              <n-descriptions-item label="ID">
                <span class="mono text-[12px]">{{ detail.id }}</span>
              </n-descriptions-item>
              <n-descriptions-item label="创建">{{ detail.createdAt || '—' }}</n-descriptions-item>
              <n-descriptions-item label="更新">{{ detail.updatedAt || '—' }}</n-descriptions-item>
            </n-descriptions>

            <n-form label-placement="left" :disabled="saving">
              <n-form-item label="名称">
                <n-input v-model:value="edit.name" placeholder="展示名" style="width: 260px" />
              </n-form-item>
              <n-form-item label="类型">
                <n-select v-model:value="edit.type" :options="TYPE_OPTIONS" style="width: 180px" />
              </n-form-item>
              <n-form-item label="启用">
                <n-switch v-model:value="edit.enabled" />
                <span class="muted text-[12px] ml-2">
                  对应后端 status：{{ edit.enabled ? 'active' : 'disabled' }}
                </span>
              </n-form-item>
              <n-form-item label="说明">
                <n-input
                  v-model:value="edit.description"
                  type="textarea"
                  :autosize="{ minRows: 2, maxRows: 5 }"
                  placeholder="用途说明"
                />
              </n-form-item>
              <n-form-item label="场景">
                <n-input
                  v-model:value="edit.scenario"
                  type="textarea"
                  :autosize="{ minRows: 2, maxRows: 5 }"
                  placeholder="适用场景，会下发进 Profile"
                />
              </n-form-item>
              <n-form-item
                label="config"
                :validation-status="configError ? 'error' : undefined"
                :feedback="configError"
              >
                <div style="width: 100%">
                  <div style="display: flex; justify-content: flex-end; margin-bottom: 4px">
                    <n-button size="tiny" quaternary :disabled="saving" @click="formatConfig">
                      格式化
                    </n-button>
                  </div>
                  <n-input
                    v-model:value="configText"
                    type="textarea"
                    :autosize="{ minRows: 4, maxRows: 12 }"
                    placeholder="{}"
                    style="font-family: ui-monospace, monospace"
                    @blur="validateConfigOnBlur"
                  />
                </div>
              </n-form-item>
            </n-form>

            <n-space>
              <n-button type="primary" :loading="saving" @click="save">保存</n-button>
              <n-button quaternary @click="requestClose">关闭</n-button>
            </n-space>
          </div>
        </n-spin>
      </n-drawer-content>
    </n-drawer>
  </div>
</template>