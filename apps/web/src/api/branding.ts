// ZY-Agents 品牌注入（S-62：前端不硬编码品牌字面量，一律从 /branding 端点读）。
// GET /api/v1/branding 是唯一免 JWT 公开端点；display_name 渲染到登录页标题，
// theme.primary/accent 注入 CSS 变量，login_title/login_welcome 覆盖默认文案。

import axios from 'axios'
import { reactive } from 'vue'
import { applyBrandMessages } from '../hooks/i18n'

/** 响应式品牌状态（头部/加载屏等消费；启动时由 /branding 填充，空串回退默认文案）。 */
export const brandState = reactive({
  display_name: '',
  short_name: '',
})

export type BrandingConfig = {
  display_name: string
  short_name: string
  theme: { primary: string; accent: string }
  login_title: string | null
  login_welcome: string | null
  footer_copyright: string | null
  help_url: string | null
  support_contact: string | null
}

const FALLBACK: BrandingConfig = {
  display_name: '',
  short_name: '',
  theme: { primary: '', accent: '' },
  login_title: null,
  login_welcome: null,
  footer_copyright: null,
  help_url: null,
  support_contact: null,
}

export async function fetchBranding(): Promise<BrandingConfig | null> {
  try {
    const response = await axios.get('/api/v1/branding', { timeout: 8000 })
    const data = response.data
    if (!data || typeof data !== 'object') return null
    return {
      ...FALLBACK,
      ...data,
      theme: { ...FALLBACK.theme, ...(data.theme || {}) },
    }
  } catch {
    return null
  }
}

/** 把品牌配置落到 DOM（CSS 变量）与 i18n 覆盖层；null 字段保留默认文案。 */
export function applyBranding(config: BrandingConfig | null): void {
  if (!config || typeof document === 'undefined') return
  const root = document.documentElement
  if (config.theme.primary) root.style.setProperty('--brand-primary', config.theme.primary)
  if (config.theme.accent) root.style.setProperty('--brand-accent', config.theme.accent)

  // 响应式品牌状态（头部/加载屏消费，端点值非硬编码）
  brandState.display_name = config.display_name || ''
  brandState.short_name = config.short_name || ''

  // 浏览器标签页标题：display_name（端点值）
  if (config.display_name) document.title = config.display_name

  // 登录页标题：login_title 优先，回退 display_name（端点值，非前端硬编码）。
  const loginTitle = config.login_title || config.display_name || ''
  const brandShort = config.short_name || config.display_name || ''
  applyBrandMessages({
    'login.title': loginTitle ? { zh: loginTitle, en: loginTitle } : null,
    'login.password_desc': config.login_welcome
      ? { zh: config.login_welcome, en: config.login_welcome }
      : null,
    // 免责声明带品牌名（端点 short_name，非硬编码）
    'chat.disclaimer': brandShort
      ? { zh: `${brandShort} 可能会犯错。重要信息请自行核验。`, en: `${brandShort} can make mistakes. Verify important info.` }
      : null,
  })
}

/** 启动入口：拉取品牌并应用；失败静默（保留默认文案）。 */
export async function initBranding(): Promise<void> {
  applyBranding(await fetchBranding())
}
