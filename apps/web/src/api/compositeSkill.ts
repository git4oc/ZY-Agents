// composite_task 技能 markdown 的前端编解码。
// 后端按同一份结构解析，两端需保持同步。

// 单个用户录制的定位器。所有条件在匹配时取 AND；每项均可缺省。
export type Locator = {
  role?: string
  name?: string
  name_contains?: string
  aria_label?: string
  icon_class?: string
  text?: string
  ancestor_role?: string
  ancestor_contains_text?: string
  nth?: number
}

// 单步的定位器集合。primary 是手动录制流程写入的默认键；
// 需要按操作区分的步骤（更新/删除等）可另写各自的键。
export type LocatorBag = Record<string, Locator>

export type CompositeStep = {
  instruction: string
  site_profile_id?: string
  kind?: string
  title?: string
  locators?: LocatorBag
  locator_vars?: Record<string, string>
}

export type CompositeSkillDraft = {
  triggers: string[]
  steps: CompositeStep[]
}

// 面向本模块窄结构的最小 YAML 输出器，避免为此引入 js-yaml。
// 只需产出标量列表与浅层对象列表；含换行等不安全字符的字符串改用块标量输出。
function isSafeInline(s: string): boolean {
  return !!s && !/[\n:#"'`{}\[\],&*!|>%@`]/.test(s) && s.trim() === s
}

function dumpScalar(value: string): string {
  if (value === '') return '""'
  if (isSafeInline(value)) return value
  const escaped = value.replace(/\\/g, '\\\\').replace(/"/g, '\\"')
  return `"${escaped}"`
}

function dumpMultiline(key: string, value: string, indent: string): string {
  // 含换行的字符串按字面块标量输出。
  const lines = value.split('\n').map(l => `${indent}  ${l}`).join('\n')
  return `${indent}${key}: |\n${lines}`
}

export function encodeCompositeSkill(
  draft: CompositeSkillDraft,
  meta: { name: string; description: string },
): string {
  const lines: string[] = ['---']
  if (meta.name) lines.push(`name: ${dumpScalar(meta.name)}`)
  if (meta.description) {
    if (meta.description.includes('\n')) {
      lines.push('description: |')
      for (const l of meta.description.split('\n')) lines.push(`  ${l}`)
    } else {
      lines.push(`description: ${dumpScalar(meta.description)}`)
    }
  }
  if (draft.triggers.length) {
    lines.push('triggers:')
    for (const t of draft.triggers) lines.push(`  - ${dumpScalar(t)}`)
  }
  lines.push('steps:')
  for (const step of draft.steps) {
    lines.push('  - instruction: ' + (step.instruction.includes('\n') ? '|' : dumpScalar(step.instruction)))
    if (step.instruction.includes('\n')) {
      for (const l of step.instruction.split('\n')) lines.push(`      ${l}`)
    }
    if (step.site_profile_id) lines.push(`    site_profile_id: ${dumpScalar(step.site_profile_id)}`)
    if (step.kind) lines.push(`    kind: ${dumpScalar(step.kind)}`)
    if (step.title) lines.push(`    title: ${dumpScalar(step.title)}`)
    if (step.locators && Object.keys(step.locators).length) {
      lines.push('    locators:')
      for (const [op, loc] of Object.entries(step.locators)) {
        if (!loc || !Object.keys(loc).length) continue
        lines.push(`      ${op}:`)
        for (const [k, v] of Object.entries(loc)) {
          if (v === undefined || v === '' || v === null) continue
          lines.push(`        ${k}: ${typeof v === 'number' ? String(v) : dumpScalar(String(v))}`)
        }
      }
    }
    if (step.locator_vars && Object.keys(step.locator_vars).length) {
      lines.push('    locator_vars:')
      for (const [k, v] of Object.entries(step.locator_vars)) {
        lines.push(`      ${k}: ${dumpScalar(String(v ?? ''))}`)
      }
    }
  }
  lines.push('---')
  return lines.join('\n') + '\n'
}

// 与 encodeCompositeSkill 对应的最小解码器。足以回读本编辑器自身产出、
// 供再次编辑的技能；手写且结构特殊的 YAML 不保证还原——服务端才是准绳。
export function decodeCompositeSkill(markdown: string): CompositeSkillDraft {
  const m = /^---\s*\n([\s\S]*?)\n---/m.exec(markdown || '')
  if (!m) return { triggers: [], steps: [] }
  const body = m[1]

  const triggers: string[] = []
  const steps: CompositeStep[] = []

  let section: 'none' | 'triggers' | 'steps' = 'none'
  let nested: 'none' | 'locators' | 'locator_vars' = 'none'
  let nestedOp = ''
  let currentStep: CompositeStep | null = null

  const flushStep = () => {
    if (currentStep && currentStep.instruction.trim()) steps.push(currentStep)
    currentStep = null
  }

  for (const rawLine of body.split(/\r?\n/)) {
    const line = rawLine
    if (/^triggers:\s*$/.test(line)) {
      flushStep(); section = 'triggers'; continue
    }
    if (/^steps:\s*$/.test(line)) {
      flushStep(); section = 'steps'; continue
    }
    if (/^[a-zA-Z_]+:\s*/.test(line) && !line.startsWith('  ')) {
      // 其余顶层键（name / description）跳过：这些值从 props 取得。
      flushStep(); section = 'none'; continue
    }

    if (section === 'triggers') {
      const mm = /^\s*-\s*(.*)$/.exec(line)
      if (mm) {
        const v = mm[1].trim().replace(/^"|"$/g, '')
        if (v) triggers.push(v)
      }
      continue
    }

    if (section === 'steps') {
      const newItem = /^\s{2}-\s*instruction:\s*(.*)$/.exec(line)
      if (newItem) {
        flushStep()
        const raw = newItem[1].trim().replace(/^"|"$/g, '')
        currentStep = { instruction: raw === '|' ? '' : raw }
        nested = 'none'; nestedOp = ''
        continue
      }
      if (currentStep) {
        const kv = /^\s{4}([a-zA-Z_]+):\s*(.*)$/.exec(line)
        if (kv) {
          const key = kv[1]
          const val = kv[2].trim().replace(/^"|"$/g, '')
          if (key === 'site_profile_id') { currentStep.site_profile_id = val; nested = 'none' }
          else if (key === 'kind') { currentStep.kind = val; nested = 'none' }
          else if (key === 'title') { currentStep.title = val; nested = 'none' }
          else if (key === 'locators') { currentStep.locators = {}; nested = 'locators'; nestedOp = '' }
          else if (key === 'locator_vars') { currentStep.locator_vars = {}; nested = 'locator_vars' }
          else { nested = 'none' }
          continue
        }
        // 嵌套定位器：缩进 6 为操作名，缩进 8 为该操作的字段。
        if (nested === 'locators') {
          const opLine = /^\s{6}([a-zA-Z_]+):\s*$/.exec(line)
          if (opLine) { nestedOp = opLine[1]; if (currentStep.locators) currentStep.locators[nestedOp] = {}; continue }
          const locKv = /^\s{8}([a-zA-Z_]+):\s*(.*)$/.exec(line)
          if (locKv && nestedOp && currentStep.locators?.[nestedOp]) {
            const k = locKv[1]
            const v = locKv[2].trim().replace(/^"|"$/g, '')
            if (k === 'nth') {
              const n = Number(v); if (!Number.isNaN(n)) (currentStep.locators[nestedOp] as any).nth = n
            } else {
              ;(currentStep.locators[nestedOp] as any)[k] = v
            }
            continue
          }
        }
        if (nested === 'locator_vars') {
          const kv2 = /^\s{6}([a-zA-Z_]+):\s*(.*)$/.exec(line)
          if (kv2) {
            const v = kv2[2].trim().replace(/^"|"$/g, '')
            if (!currentStep.locator_vars) currentStep.locator_vars = {}
            currentStep.locator_vars[kv2[1]] = v
            continue
          }
        }
        // 多行指令的续行（缩进 6 以上）
        const cont = /^\s{6}(.*)$/.exec(line)
        if (cont && nested === 'none') {
          currentStep.instruction = (currentStep.instruction
            ? currentStep.instruction + '\n'
            : '') + cont[1]
        }
      }
    }
  }
  flushStep()
  return { triggers, steps }
}
