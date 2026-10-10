import type { SkillItem } from '../../api/skills'
import { t } from '../../hooks/i18n'

export function skillSourceLabel(skill: SkillItem): string {
  const source = skill.packageSource
  if (!source?.kind) return ''
  if (source.kind === 'shared') {
    const sender = source.sender?.displayName || source.sender?.username || ''
    return sender
      ? t('skills.detail.source_shared_by', { name: sender })
      : t('skills.detail.source_shared')
  }
  if (source.kind === 'skillhub') {
    return source.coordinate
      ? t('skills.detail.source_market_named', { name: source.coordinate })
      : t('skills.detail.source_market')
  }
  if (source.kind === 'local_zip') {
    return source.fileName
      ? t('skills.detail.source_zip_named', { name: source.fileName })
      : t('skills.detail.source_zip')
  }
  return t('skills.detail.source_imported')
}
