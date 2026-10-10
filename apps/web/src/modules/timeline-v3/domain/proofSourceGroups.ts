import type { EvidenceSourceItem } from './delivery'

export interface ProofSourceGroup {
  key: string
  primary: EvidenceSourceItem
  sources: EvidenceSourceItem[]
  isDocument: boolean
}

function sourceIdentity(source: EvidenceSourceItem, index: number): string {
  const documentId = String(source.document_id || '').trim()
  if (documentId) return `document:${documentId}`

  const sourceUrl = String(source.source_url || '').trim()
  if (sourceUrl) return `url:${sourceUrl}`

  const sourceId = String(source.id || source.citation_id || '').trim()
  return sourceId ? `source:${sourceId}` : `source-index:${index}`
}

export function groupEvidenceSources(sources: EvidenceSourceItem[]): ProofSourceGroup[] {
  const groups: ProofSourceGroup[] = []
  const groupByKey = new Map<string, ProofSourceGroup>()

  sources.forEach((source, index) => {
    const key = sourceIdentity(source, index)
    const existing = groupByKey.get(key)
    if (existing) {
      existing.sources.push(source)
      return
    }

    const group: ProofSourceGroup = {
      key,
      primary: source,
      sources: [source],
      isDocument: Boolean(String(source.document_id || '').trim()),
    }
    groups.push(group)
    groupByKey.set(key, group)
  })

  return groups
}

export function evidenceSourceStats(sources: EvidenceSourceItem[]) {
  const groups = groupEvidenceSources(sources)
  let web = 0
  let internal = 0
  let fragments = 0

  for (const group of groups) {
    const type = String(group.primary.source_type || '')
    if (type === 'web') web += 1
    else internal += 1
    if (group.isDocument) fragments += group.sources.length
  }

  return {
    groups,
    total: groups.length,
    web,
    internal,
    fragments,
  }
}
