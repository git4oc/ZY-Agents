import assert from 'node:assert/strict'
import { builtInShortcutEntries, effectiveShortcutEntries, resolveShortcutGuideCategories } from '../src/ui/chat/shortcutGuideResolver'
import type { EffectiveShortcuts, ShortcutEntry } from '../src/api/shortcuts'

const personal: ShortcutEntry = {
  id: 'personal.test', type: 'prompt', categoryKey: 'personal', categoryLabel: '我的常用',
  label: '测试入口', iconKey: 'document', prompt: '测试提示词', resourceId: '', enabled: true,
}

const unconfigured: EffectiveShortcuts = {
  configured: false, groups: [], entries: [],
  preferences: { groupOrders: { personal: ['personal.test'] }, personalGroup: { key: 'personal', label: '我的常用' }, personalEntries: [personal] },
}
const fallback = effectiveShortcutEntries(unconfigured)
assert.equal(fallback.length, builtInShortcutEntries().length + 1)
assert.equal(fallback.at(-1)?.id, 'personal.test')

const groups = resolveShortcutGuideCategories(fallback, false, [...unconfigured.groups, unconfigured.preferences.personalGroup!])
assert.equal(groups.some(group => group.key === 'personal'), true)
assert.equal(groups.some(group => group.key === 'favorites'), false)

const configured = { ...unconfigured, configured: true, entries: [personal] }
assert.deepEqual(effectiveShortcutEntries(configured), [personal])

const orderedEntries: ShortcutEntry[] = [
  { ...personal, id: 'group-b.item', categoryKey: 'group-b', categoryLabel: 'B' },
  { ...personal, id: 'group-a.item', categoryKey: 'group-a', categoryLabel: 'A' },
]
const orderedGroups = resolveShortcutGuideCategories(orderedEntries, true, [
  { key: 'group-a', label: 'A' }, { key: 'group-b', label: 'B' },
])
assert.deepEqual(orderedGroups.map(group => group.key), ['group-a', 'group-b'])

console.log('shortcut guide tests passed')
