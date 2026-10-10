import type { EffectiveShortcuts, ShortcutEntry, ShortcutGroup, ShortcutPreferences } from '../../api/shortcuts'
import { promptGuideCategories, promptGuideIcons, type PromptGuideCategory } from './promptGuideConfig'

const iconFallback = promptGuideIcons.document
const categoryMeta = new Map(promptGuideCategories.map(category => [category.key, category]))
const entryIds = new Map([
  ['微信/小红书', 'content.wechat'], ['报告方案', 'content.report'], ['PPT生成', 'content.ppt'],
  ['图片转PRD', 'content.image_prd'], ['文档翻译', 'content.translate'], ['文档填表', 'content.fill_form'],
  ['联网查资料', 'external.web'], ['行业研究', 'external.industry'], ['竞品分析', 'external.competitor'],
  ['政策/新闻', 'external.policy_news'], ['资料综述', 'external.overview'], ['事实核查', 'external.fact_check'],
  ['搜知识库', 'internal.search'], ['查历史文档', 'internal.history'], ['查企业制度', 'internal.policy'],
  ['查项目资料', 'internal.project'], ['查客户资料', 'internal.customer'], ['查培训材料', 'internal.training'],
  ['查销售', 'systems.sales'], ['找根因', 'systems.root_cause'], ['查库存', 'systems.inventory'],
  ['查客户', 'systems.customer'], ['经营日报', 'systems.daily'], ['调用MCP', 'systems.mcp'],
])

export function builtInShortcutEntries(): ShortcutEntry[] {
  return promptGuideCategories.flatMap(category => category.items.map(item => ({
    id: entryIds.get(item.label) || `${category.key}.${item.label}`,
    type: 'prompt' as const,
    categoryKey: category.key,
    categoryLabel: category.label,
    label: item.label,
    iconKey: iconKeyFor(item.icon),
    prompt: item.prompt || '',
    resourceId: '',
    enabled: item.available,
    locked: false,
  })))
}

export function effectiveShortcutEntries(result: EffectiveShortcuts): ShortcutEntry[] {
  if (result.configured) return result.entries
  return applyPreferences(builtInShortcutEntries(), result.preferences)
}

export function resolveShortcutGuideCategories(entries: ShortcutEntry[], configured: boolean, configuredGroups: ShortcutGroup[] = []): PromptGuideCategory[] {
  if (!configured && !entries.length) return promptGuideCategories

  const groups = new Map<string, PromptGuideCategory>()
  const configuredGroupMeta = new Map(configuredGroups.map(group => [group.key, group]))
  for (const entry of entries) {
    if (entry.hidden || entry.enabled === false) continue
    const base = categoryMeta.get(entry.categoryKey)
    const configuredGroup = configuredGroupMeta.get(entry.categoryKey)
    let group = groups.get(entry.categoryKey)
    if (!group) {
      group = {
        key: entry.categoryKey,
        label: configuredGroup?.label || entry.categoryLabel,
        icon: safeSvg(entry.categoryIconSvg) || safeSvg(configuredGroup?.iconSvg) || promptGuideIcons[entry.categoryIconKey as keyof typeof promptGuideIcons] || (configuredGroup?.iconKey ? promptGuideIcons[configuredGroup.iconKey as keyof typeof promptGuideIcons] : '') || base?.icon || promptGuideIcons.grid,
        tone: base?.tone || 'guide-tone-content',
        requiresConfig: base?.requiresConfig,
        configTarget: base?.configTarget,
        configTitle: base?.configTitle,
        configDescription: base?.configDescription,
        items: [],
      }
      groups.set(entry.categoryKey, group)
    }
    const item = {
      label: entry.label,
      prompt: entry.prompt || resourcePrompt(entry),
    icon: safeSvg(entry.iconSvg) || promptGuideIcons[entry.iconKey as keyof typeof promptGuideIcons] || iconFallback,
      available: true,
      categoryKey: entry.categoryKey,
      shortcutType: entry.type,
      resourceId: entry.resourceId,
    }
    group.items.push(item)
  }
  const orderedKeys = [
    ...(configured ? configuredGroups.map(group => group.key) : promptGuideCategories.map(group => group.key)),
    ...groups.keys(),
  ].filter((key, index, all) => all.indexOf(key) === index)
  const orderedGroups = orderedKeys.map(key => groups.get(key)).filter((group): group is PromptGuideCategory => !!group)
  return orderedGroups.filter(group => group.items.length)
}

function safeSvg(value?: string): string {
  const svg = String(value || '').trim()
  if (!svg || !/^<svg\b/i.test(svg) || !/<\/svg>$/i.test(svg)) return ''
  if (/<script\b|<foreignObject\b|\son[a-z]+\s*=|javascript:/i.test(svg)) return ''
  return svg
}

function applyPreferences(entries: ShortcutEntry[], preferences: ShortcutPreferences): ShortcutEntry[] {
  const all = [...entries, ...(preferences.personalEntries || []).map(item => ({ ...item, personal: true }))]
  const keys = [...new Set(all.map(item => item.categoryKey))]
  return keys.flatMap(key => {
    const group = all.filter(item => item.categoryKey === key)
    const order = new Map((preferences.groupOrders?.[key] || []).map((id, index) => [id, index]))
    return group.map((item, index) => ({ item, index })).sort((a, b) =>
      (order.get(a.item.id) ?? 10_000) - (order.get(b.item.id) ?? 10_000) || a.index - b.index
    ).map(row => row.item)
  })
}

function iconKeyFor(icon: string): string {
  return Object.entries(promptGuideIcons).find(([, value]) => value === icon)?.[0] || 'document'
}

function resourcePrompt(entry: ShortcutEntry): string {
  if (entry.type === 'skill') return `请使用 Skill「${entry.label}」完成以下任务：`
  return ''
}
