// ZY-Agents 阶段 1 无 shortcuts 端点：stub 为空数据（保持签名），
// 快捷指令区自然空态。

export type ShortcutEntryType = 'prompt' | 'skill'

export interface ShortcutEntry {
  id: string
  type: ShortcutEntryType
  categoryKey: string
  categoryLabel: string
  label: string
  iconKey: string
  iconSvg?: string
  categoryIconKey?: string
  categoryIconSvg?: string
  prompt: string
  resourceId: string
  enabled?: boolean
  locked?: boolean
  hidden?: boolean
  favorite?: boolean
  personal?: boolean
}

export interface ShortcutPreferences {
  groupOrders: Record<string, string[]>
  personalGroup: ShortcutGroup | null
  personalEntries: ShortcutEntry[]
}

export interface ShortcutGroup {
  key: string
  label: string
  iconKey?: string
  iconSvg?: string
  locked?: boolean
  personal?: boolean
}

export interface EffectiveShortcuts {
  entries: ShortcutEntry[]
  groups: ShortcutGroup[]
  defaultOrder?: string[]
  configured: boolean
  schemeKey?: string
  preferences: ShortcutPreferences
}

const EMPTY_EFFECTIVE: EffectiveShortcuts = {
  entries: [],
  groups: [],
  configured: false,
  preferences: {
    groupOrders: {},
    personalGroup: null,
    personalEntries: [],
  },
}

export async function fetchEffectiveShortcuts(): Promise<EffectiveShortcuts> {
  return EMPTY_EFFECTIVE
}

export async function saveShortcutPreferences(_payload: ShortcutPreferences): Promise<EffectiveShortcuts> {
  return EMPTY_EFFECTIVE
}
