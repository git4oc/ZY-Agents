/**
 * 组织单元的编码生成与层级推导（纯函数，无副作用）。
 *
 * ## 为什么要自动生成 code
 *
 * `org_units` 的唯一索引是 `org_units_code_u = {code: 1}`——**全局唯一**，
 * 且后端 `OrgUnitsRepository.create` 不做重名预检，只靠插入时索引报错。
 * 让人手填 `east-rd` 这类编码既费力又容易撞车，而错误信息是 Mongo 的重复键
 * 异常（对使用者不可读）。所以编码改为**建议 + 可覆盖**：前端按规则生成，
 * 用户仍可改；后端索引继续作为最后一道防线。
 *
 * ## 为什么不做拼音转换
 *
 * 中文名转拼音需要额外依赖，且多音字（"行"、"重"）无法自动定音——猜错的编码
 * 比 `org-001` 更糟（它看起来有意义却是错的）。所以规则只提取名称里**本来就有的**
 * 拉丁字母/数字片段，没有就退回 `org`。用户想要 `east-rd` 时，直接在「编码前缀」
 * 里填 `east-rd` 即可。
 */

/** 组织单元行（后端 `GET /admin/org-units` 返回的原始文档，最小必需字段）。 */
export interface OrgUnitRow {
  _id?: unknown
  id?: unknown
  name?: unknown
  code?: unknown
  parent_id?: unknown
}

export interface OrgUnitOption {
  /** 组织单元 id（根节点也有自己的 id，只是 depth 为 0；"不选父级"用 null 表达，不在这里） */
  value: string
  label: string
  /** 层级深度，根为 0；用于渲染缩进 */
  depth: number
}

/** 稳定的行 id：与 `api/admin.ts` 的 `docId` 同口径。 */
export function orgUnitId(row: OrgUnitRow): string {
  const raw = row._id ?? row.id
  return raw === undefined || raw === null ? '' : String(raw)
}

/**
 * 从名称里提取编码前缀候选。
 *
 * 规则：取名称中的拉丁字母/数字片段，**其余字符（含中文）一律丢弃**而不是转连字符——
 * 否则"华东研发中心"会变成 `---`，比直接用 `org` 更糟。多片段用 `-` 连接，
 * 连续分隔符合并，长度上限 32（与后端 `OrgUnitBody.code` 无长度约束，但过长不便读）。
 */
export function suggestCodePrefix(name: string): string {
  const segments = (name || '')
    .split(/[^a-zA-Z0-9]+/)
    .map((s) => s.toLowerCase())
    .filter(Boolean)
  const joined = segments.join('-').slice(0, 32)
  return joined || 'org'
}

/**
 * 生成建议编码 `{prefix}-{NNN}`。
 *
 * 序号取 `existingCodes` 中匹配该前缀的最大序号 +1，三位补零；前缀本身已存在
 * 但没有带序号的同族编码时从 1 起。找不到可用序号时（例如前缀下序号已连续占用
 * 超过 999）回退到 4 位补零，避免给出必然重复的值。
 */
export function suggestOrgUnitCode(name: string, existingCodes: string[], prefixOverride?: string): string {
  const prefix = (prefixOverride ?? '').trim() || suggestCodePrefix(name)
  const codes = new Set(existingCodes)

  // 收集 `prefix-<数字>` 形态的同族编码，取最大序号
  const pattern = new RegExp(`^${escapeRegExp(prefix)}-(\\d+)$`)
  let maxSeq = 0
  for (const code of codes) {
    const matched = pattern.exec(code)
    if (!matched) continue
    const seq = Number(matched[1])
    if (Number.isFinite(seq) && seq > maxSeq) maxSeq = seq
  }

  for (let seq = maxSeq + 1; seq < maxSeq + 1000; seq += 1) {
    const width = seq > 999 ? 4 : 3
    const candidate = `${prefix}-${String(seq).padStart(width, '0')}`
    if (!codes.has(candidate)) return candidate
  }
  // 理论上不可达（同一前缀下不会有 1000 个已占用序号），但仍给出兜底而非死循环
  return `${prefix}-${Date.now().toString(36)}`
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

/**
 * 把扁平的组织单元列表推导成**有序、带深度**的层级序列（DFS 前序）。
 *
 * 为什么需要它：后端 `GET /admin/org-units` 返回扁平数组且**不保证顺序**，
 * 直接丢给下拉框会出现"子节点排在父节点前面"，用户没法理解层级。
 *
 * 环与孤儿的处理：后端 `create` 不校验 `parent_id` 是否存在/是否成环，
 * 所以这里必须自己兜住——找不到父节点的行按**根节点**处理（depth 0），
 * 并对已在访问链上的节点停止下钻，避免环形数据导致无限递归爆栈。
 */
export function buildOrgUnitHierarchy(rows: OrgUnitRow[]): OrgUnitOption[] {
  const nodes = rows.map((row) => ({
    id: orgUnitId(row),
    name: String(row.name ?? ''),
    code: String(row.code ?? ''),
    parent: row.parent_id === undefined || row.parent_id === null ? null : String(row.parent_id),
  }))

  const byParent = new Map<string, typeof nodes>()
  const known = new Set(nodes.map((n) => n.id))
  for (const node of nodes) {
    // 父级不存在（被删或跨 org 残留）→ 归到根节点分组
    const key = node.parent !== null && known.has(node.parent) ? node.parent : ''
    const bucket = byParent.get(key)
    if (bucket) bucket.push(node)
    else byParent.set(key, [node])
  }

  const options: OrgUnitOption[] = []
  const visited = new Set<string>()

  const walk = (key: string, depth: number, path: Set<string>): void => {
    for (const node of byParent.get(key) ?? []) {
      if (path.has(node.id)) continue // 环形引用：停在这里，不下钻
      visited.add(node.id)
      options.push({
        value: node.id,
        label: `${node.name || '(未命名)'}${node.code ? ` (${node.code})` : ''}`,
        depth,
      })
      const nextPath = new Set(path)
      nextPath.add(node.id)
      walk(node.id, depth + 1, nextPath)
    }
  }
  walk('', 0, new Set())

  // 兜底：因环/自引用而未被遍历到的节点，补在末尾（标注深度 0），不静默丢弃
  for (const node of nodes) {
    if (!visited.has(node.id)) {
      options.push({
        value: node.id,
        label: `${node.name || '(未命名)'}${node.code ? ` (${node.code})` : ''}（层级异常）`,
        depth: 0,
      })
    }
  }
  return options
}

/** 按 id 找名称（列表里展示「父级名称」而不是裸 ObjectId）。 */
export function orgUnitNameById(rows: OrgUnitRow[], id: unknown): string {
  const key = id === undefined || id === null ? '' : String(id)
  if (!key) return '—'
  const hit = rows.find((row) => orgUnitId(row) === key)
  return hit ? String(hit.name ?? '') || '—' : '（已删除）'
}