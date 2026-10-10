<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import {
  NButton,
  NCheckbox,
  NColorPicker,
  NDescriptions,
  NDescriptionsItem,
  NInput,
  NUpload,
  useMessage,
  type UploadCustomRequestOptions,
} from 'naive-ui'
import {
  applyBrandingState,
  ASSET_SLOTS,
  deleteBrandingAsset,
  fetchBrandingSettings,
  patchBrandingSettings,
  uploadBrandingAsset,
  type AssetSlot,
  type BrandingSettings,
} from '../api/branding'
import { errorMessage, isPermissionDenied } from '../api/client'

const message = useMessage()

const loading = ref(false)
const saving = ref(false)
const denied = ref(false)
const error = ref('')
const settings = ref<BrandingSettings | null>(null)

// 可编辑字段：与后端 PATCH 端点实际读取的字段一一对应
const form = ref({
  display_name: '',
  short_name: '',
  primary: '#2563eb',
  accent: '#0ea5e9',
  vendor_badge_enabled: true,
})

const SLOT_LABEL: Record<AssetSlot, string> = {
  logo_wide: '横向 Logo（页眉）',
  logo_square: '方形 Logo（头像/图标位）',
  favicon: '站点图标 favicon',
}

const SLOT_ACCEPT: Record<AssetSlot, string> = {
  logo_wide: 'image/png,image/jpeg,image/svg+xml',
  logo_square: 'image/png,image/jpeg,image/svg+xml',
  favicon: 'image/png,image/x-icon,image/svg+xml',
}

const busySlot = ref<AssetSlot | null>(null)

const updatedAtText = computed(() => {
  const value = settings.value?.updated_at
  if (!value) return '—'
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString()
})

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  denied.value = false
  try {
    const data = await fetchBrandingSettings()
    settings.value = data
    applyBrandingState(data)
    form.value = {
      display_name: data.display_name,
      short_name: data.short_name,
      primary: data.theme.primary,
      accent: data.theme.accent,
      vendor_badge_enabled: data.vendor_badge_enabled,
    }
  } catch (err) {
    settings.value = null
    denied.value = isPermissionDenied(err)
    error.value = denied.value
      ? `${errorMessage(err)} —— 品牌管理需要 brand.manage 角色码`
      : errorMessage(err)
  } finally {
    loading.value = false
  }
}

async function save(): Promise<void> {
  if (!form.value.display_name.trim() || !form.value.short_name.trim()) {
    message.warning('公司全称与简称都不能为空')
    return
  }
  saving.value = true
  try {
    const updated = await patchBrandingSettings({
      display_name: form.value.display_name.trim(),
      short_name: form.value.short_name.trim(),
      vendor_badge_enabled: form.value.vendor_badge_enabled,
      theme: { primary: form.value.primary, accent: form.value.accent },
    })
    settings.value = updated
    // 保存后即时同步：侧栏品牌名 + CSS 主题色立即生效，无需刷新
    applyBrandingState(updated)
    message.success('品牌配置已保存（公开端点 /branding 立即生效）')
  } catch (err) {
    message.error(errorMessage(err, '保存失败'))
  } finally {
    saving.value = false
  }
}

async function upload(slot: AssetSlot, options: UploadCustomRequestOptions): Promise<void> {
  const file = options.file
  if (!(file instanceof File)) {
    message.error('未选择文件')
    return
  }
  busySlot.value = slot
  try {
    await uploadBrandingAsset(slot, file)
    message.success(`${SLOT_LABEL[slot]} 已上传`)
    await load()
  } catch (err) {
    message.error(errorMessage(err, '上传失败'))
  } finally {
    busySlot.value = null
  }
}

async function removeAsset(slot: AssetSlot): Promise<void> {
  busySlot.value = slot
  try {
    await deleteBrandingAsset(slot)
    message.success(`${SLOT_LABEL[slot]} 已移除`)
    await load()
  } catch (err) {
    message.error(errorMessage(err, '移除失败'))
  } finally {
    busySlot.value = null
  }
}

onMounted(load)
</script>

<template>
  <div>
    <!-- 错误提示 -->
    <div
      v-if="error"
      class="mb-4 flex items-start gap-3 rounded-lg border px-3.5 py-2.5 text-[13px]"
      :class="denied ? 'border-amber-200 bg-amber-50 text-amber-800' : 'border-red-200 bg-red-50 text-red-700'"
    >
      <span class="flex-1">{{ error }}</span>
      <button
        class="shrink-0 rounded-md px-2 py-0.5 text-xs font-medium hover:bg-black/5 cursor-pointer"
        @click="load"
      >重试</button>
    </div>

    <!-- 基础品牌 -->
    <section class="panel">
      <!-- 标题行：右侧常驻保存按钮（始终可见，无需滚动） -->
      <div class="flex flex-wrap items-center gap-3 mb-4 pb-3 border-b border-slate-100">
        <div class="flex-1 min-w-0">
          <h2 class="panel-title">基础品牌</h2>
          <p class="text-xs text-slate-500 truncate">
            <span class="mono">PATCH /api/v1/admin/branding</span> · 公司全称、简称、主色、辅色、厂商标识开关 · 需
            <span class="mono">brand.manage</span>
          </p>
        </div>
        <n-button quaternary size="small" :disabled="saving" @click="load">放弃修改</n-button>
        <n-button type="primary" size="small" :loading="saving" @click="save">保存</n-button>
      </div>

      <n-form :disabled="saving || !settings" label-placement="top">
        <div class="grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-1 max-w-3xl">
          <n-form-item label="公司全称（display_name）">
            <n-input v-model:value="form.display_name" placeholder="展示在登录页与浏览器标题" />
          </n-form-item>
          <n-form-item label="简称（short_name）">
            <n-input v-model:value="form.short_name" placeholder="侧边栏与窄屏使用" />
          </n-form-item>
          <n-form-item label="主色 primary">
            <n-color-picker v-model:value="form.primary" :show-alpha="false" :modes="['hex']" />
          </n-form-item>
          <n-form-item label="辅色 accent">
            <n-color-picker v-model:value="form.accent" :show-alpha="false" :modes="['hex']" />
          </n-form-item>
        </div>
        <n-checkbox v-model:checked="form.vendor_badge_enabled" :disabled="saving">
          页脚厂商标识（vendor_badge_enabled）
        </n-checkbox>
      </n-form>
    </section>

    <!-- 品牌文案（只读） -->
    <section class="panel">
      <h2 class="panel-title">品牌文案（只读）</h2>
      <p class="panel-hint">
        以下字段属于 <span class="mono">BrandingSettings</span>，但后端 PATCH 端点当前**未接线**这些字段
        （只读取上面几项），因此只读展示，避免"保存成功但没生效"的假象。
      </p>
      <n-descriptions :column="2" size="small" bordered>
        <n-descriptions-item label="登录页标题 login_title">{{ settings?.login_title || '（未设置）' }}</n-descriptions-item>
        <n-descriptions-item label="登录页欢迎语 login_welcome">{{ settings?.login_welcome || '（未设置）' }}</n-descriptions-item>
        <n-descriptions-item label="页脚版权 footer_copyright">{{ settings?.footer_copyright || '（未设置）' }}</n-descriptions-item>
        <n-descriptions-item label="帮助链接 help_url">{{ settings?.help_url || '（未设置）' }}</n-descriptions-item>
        <n-descriptions-item label="支持联系方式 support_contact">{{ settings?.support_contact || '（未设置）' }}</n-descriptions-item>
        <n-descriptions-item label="最后更新">
          <span class="mono">{{ updatedAtText }}</span>
          <span v-if="settings?.updated_by" class="muted mono"> · by {{ settings.updated_by }}</span>
        </n-descriptions-item>
      </n-descriptions>
    </section>

    <!-- 品牌资产 -->
    <section class="panel">
      <h2 class="panel-title">品牌资产</h2>
      <p class="panel-hint">
        对应 <span class="mono">PUT/DELETE /api/v1/admin/branding/assets/{slot}</span>，slot ∈
        logo_wide / logo_square / favicon。
      </p>
      <div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
        <div
          v-for="slot in ASSET_SLOTS"
          :key="slot"
          class="flex flex-col gap-2 rounded-lg border border-slate-200 p-3"
        >
          <div class="font-medium text-[13px] text-slate-800">
            {{ SLOT_LABEL[slot] }}
            <span class="mono muted">{{ slot }}</span>
          </div>
          <div v-if="settings?.[slot]" class="muted mono text-xs break-all">
            {{ settings[slot]?.content_type }} · {{ settings[slot]?.bytes }} B
            <template v-if="settings[slot]?.width">
              · {{ settings[slot]?.width }}×{{ settings[slot]?.height }}
            </template>
          </div>
          <div v-else class="muted text-xs">未设置</div>
          <div class="flex items-center gap-2">
            <n-tag v-if="settings?.[slot]" size="small" :bordered="false" type="success">已配置</n-tag>
            <n-tag v-else size="small" :bordered="false">空</n-tag>
            <div class="flex-1"></div>
            <n-upload
              :accept="SLOT_ACCEPT[slot]"
              :show-file-list="false"
              :disabled="busySlot === slot || !settings"
              :custom-request="(options: UploadCustomRequestOptions) => upload(slot, options)"
            >
              <n-button size="tiny" :loading="busySlot === slot">上传</n-button>
            </n-upload>
            <n-button
              size="tiny"
              quaternary
              type="error"
              :disabled="!settings?.[slot] || busySlot === slot"
              @click="removeAsset(slot)"
            >
              移除
            </n-button>
          </div>
        </div>
      </div>
    </section>
  </div>
</template>
