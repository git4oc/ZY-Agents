type WorkflowNode = {
  id?: string;
  type: string;
  outputAlias?: string;
  businessConfig?: Record<string, any>;
};

export function bindReviewSources(nodes: WorkflowNode[]): string | null {
  for (const [index, node] of nodes.entries()) {
    if (node.type !== 'review_check') continue;
    const config = node.businessConfig || {};
    const upstream = nodes.slice(0, index).map(item => ({
      id: String(item.id || ''), alias: String(item.outputAlias || item.businessConfig?.outputAlias || '').trim(),
    })).filter(item => item.id && item.alias);
    const criteriaMode = String(config.reviewCriteriaMode || (
      !config.reviewCriteriaNodeId && String(config.reviewCriteria || '').trim()
      && !upstream.some(item => item.alias === config.reviewCriteria) ? 'inline' : 'upstream'
    ));
    config.reviewCriteriaMode = criteriaMode;
    const refs = criteriaMode === 'inline'
      ? [['reviewSubjectNodeId', 'reviewSubject']]
      : [['reviewSubjectNodeId', 'reviewSubject'], ['reviewCriteriaNodeId', 'reviewCriteria']];
    for (const [idKey, labelKey] of refs) {
      const currentId = String(config[idKey] || '');
      const match = currentId
        ? upstream.find(item => item.id === currentId)
        : upstream.find(item => item.alias === config[labelKey]);
      if (!match) return node.id || `node_${index + 1}`;
      config[idKey] = match.id;
      config[labelKey] = match.alias;
    }
    if (criteriaMode === 'inline') {
      if (!String(config.reviewCriteria || '').trim()) return node.id || `node_${index + 1}`;
      config.reviewCriteriaNodeId = '';
    }
  }
  return null;
}
