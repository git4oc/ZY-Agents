import { t } from '../../hooks/i18n'

export function skillActionErrorMessage(error: any, fallbackKey: string): string {
  const data = error?.response?.data
  const detail = data?.detail
  const code = typeof detail === 'object' ? String(detail?.code || '') : ''
  if (code) {
    const key = `skills.error.${code}`
    const translated = t(key)
    if (translated !== key) return translated
  }
  if (typeof detail === 'string' && detail.trim()) return detail
  if (!code && typeof detail?.message === 'string' && detail.message.trim()) return detail.message
  // 全局错误处理器形状：{ error: { code, message, ... } }（core/errors.py）
  const errCode = typeof data?.error?.code === 'string' ? data.error.code : ''
  if (errCode) {
    const key = `skills.error.${errCode}`
    const translated = t(key)
    if (translated !== key) return translated
  }
  if (typeof data?.error?.message === 'string' && data.error.message.trim()) return data.error.message
  return t(fallbackKey)
}
