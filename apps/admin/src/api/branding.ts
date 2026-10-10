import { ref } from 'vue'
import { http } from './client'

/** 响应式品牌状态：AdminShell 侧栏/标题消费；保存或加载后由 applyBrandingState 即时更新。 */
export const brandState = ref({
  displayName: '管理端',
  shortName: '',
  primary: '#2563eb',
  accent: '#0ea5e9',
})

/** 把品牌配置写入响应式状态 + CSS 变量（品牌名与主题色即时生效，无需刷新）。 */
export function applyBrandingState(config: { display_name: string; short_name: string; theme: ThemeColors }): void {
  brandState.value = {
    displayName: config.display_name || '管理端',
    shortName: config.short_name,
    primary: config.theme.primary,
    accent: config.theme.accent,
  }
  document.documentElement.style.setProperty('--brand-primary', config.theme.primary)
  document.documentElement.style.setProperty('--brand-accent', config.theme.accent)
}

export const ASSET_SLOTS = ['logo_wide', 'logo_square', 'favicon'] as const
export type AssetSlot = (typeof ASSET_SLOTS)[number]

export type ThemeColors = {
  primary: string
  accent: string
}

export type BrandingAsset = {
  storage_key?: string
  content_type?: string
  bytes?: number
  width?: number | null
  height?: number | null
} | null

/** GET /admin/branding → BrandingSettings.model_dump()（完整视图，非白名单）。 */
export type BrandingSettings = {
  display_name: string
  short_name: string
  theme: ThemeColors
  vendor_badge_enabled: boolean
  login_title: string | null
  login_welcome: string | null
  footer_copyright: string | null
  help_url: string | null
  support_contact: string | null
  logo_wide: BrandingAsset
  logo_square: BrandingAsset
  favicon: BrandingAsset
  updated_at?: string | null
  updated_by?: string | null
}

/**
 * PATCH /admin/branding 当前**实际生效**的字段（端点只读取这四项，
 * 其余 BrandingPatch 字段端点尚未接线，UI 只能只读展示，避免"保存成功但没变"）。
 */
export type BrandingPatchInput = {
  display_name?: string
  short_name?: string
  vendor_badge_enabled?: boolean
  theme?: ThemeColors
}

function normalizeSettings(raw: unknown): BrandingSettings {
  const data = (raw || {}) as Partial<BrandingSettings>
  const theme = (data.theme || {}) as Partial<ThemeColors>
  return {
    display_name: String(data.display_name ?? ''),
    short_name: String(data.short_name ?? ''),
    theme: {
      primary: String(theme.primary ?? '#2563eb'),
      accent: String(theme.accent ?? '#0ea5e9'),
    },
    vendor_badge_enabled: data.vendor_badge_enabled !== false,
    login_title: data.login_title ?? null,
    login_welcome: data.login_welcome ?? null,
    footer_copyright: data.footer_copyright ?? null,
    help_url: data.help_url ?? null,
    support_contact: data.support_contact ?? null,
    logo_wide: data.logo_wide ?? null,
    logo_square: data.logo_square ?? null,
    favicon: data.favicon ?? null,
    updated_at: data.updated_at ?? null,
    updated_by: data.updated_by ?? null,
  }
}

/** GET /branding —— 公开端点（免 JWT），用于登录页、浏览器标题与主题色。 */
export async function fetchPublicBranding(): Promise<{
  display_name: string
  short_name: string
  theme: ThemeColors
} | null> {
  const response = await http.get('/branding')
  const data = (response.data || {}) as {
    display_name?: string
    short_name?: string
    theme?: Partial<ThemeColors>
  }
  return {
    display_name: String(data.display_name ?? ''),
    short_name: String(data.short_name ?? ''),
    theme: {
      primary: String(data.theme?.primary ?? '#2563eb'),
      accent: String(data.theme?.accent ?? '#0ea5e9'),
    },
  }
}

/** GET /admin/branding —— 需 brand.manage 角色码。 */
export async function fetchBrandingSettings(): Promise<BrandingSettings> {
  const response = await http.get('/admin/branding')
  return normalizeSettings(response.data)
}

/** PATCH /admin/branding —— 需 brand.manage 角色码。 */
export async function patchBrandingSettings(patch: BrandingPatchInput): Promise<BrandingSettings> {
  const response = await http.patch('/admin/branding', patch)
  return normalizeSettings(response.data)
}

/** PUT /admin/branding/assets/{slot} —— multipart 上传（logo_wide / logo_square / favicon）。 */
export async function uploadBrandingAsset(slot: AssetSlot, file: File): Promise<BrandingAsset> {
  const form = new FormData()
  form.append('file', file)
  const response = await http.put(`/admin/branding/assets/${slot}`, form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
  const data = (response.data || {}) as { asset?: BrandingAsset }
  return data.asset ?? null
}

/** DELETE /admin/branding/assets/{slot} */
export async function deleteBrandingAsset(slot: AssetSlot): Promise<void> {
  await http.delete(`/admin/branding/assets/${slot}`)
}
