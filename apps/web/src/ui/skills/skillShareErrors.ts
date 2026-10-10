import { t } from '../../hooks/i18n'

export function skillShareErrorMessage(error: any, fallbackKey: string): string {
  const detail = error?.response?.data?.detail
  const code = String(detail?.code || '')
  const key = `skills.share.error.${code}`
  const translated = t(key)
  if (translated !== key) return translated
  return t(fallbackKey)
}
