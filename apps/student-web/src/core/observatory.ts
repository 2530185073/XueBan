import type { KnowledgeGraphEdge, KnowledgeGraphNode } from './learning'

export interface ObservatoryPlan {
  chapterId: string
  chapterName: string
  chapterNodes: KnowledgeGraphNode[]
  focus: KnowledgeGraphNode
  relationNames: string[]
}

/** Choose a deterministic, honest tour: real attention records first, never an invented weakness. */
export function createObservatoryPlan(nodes: KnowledgeGraphNode[], edges: KnowledgeGraphEdge[]): ObservatoryPlan | null {
  if (!nodes.length) return null
  const ordered = [...nodes].sort((a, b) => a.sort_order - b.sort_order || a.id.localeCompare(b.id))
  const groups = new Map<string, KnowledgeGraphNode[]>()
  for (const node of ordered) {
    const id = node.chapter_id ?? 'unassigned'
    groups.set(id, [...(groups.get(id) ?? []), node])
  }
  const chapters = [...groups.values()].map((chapterNodes, index) => ({
    chapterNodes,
    index,
    needs: chapterNodes.filter(node => node.learning_status === 'needs_help_now').length,
    recorded: chapterNodes.filter(node => node.valid_evidence_count > 0).length,
  }))
  chapters.sort((a, b) => b.needs - a.needs || b.recorded - a.recorded || a.index - b.index)
  const chapterNodes = chapters[0].chapterNodes
  const focus = chapterNodes.find(node => node.learning_status === 'needs_help_now')
    ?? chapterNodes.find(node => node.learning_status === 'consolidating')
    ?? chapterNodes.find(node => node.valid_evidence_count > 0)
    ?? chapterNodes[0]
  const related = new Set<string>()
  for (const edge of edges) {
    if (edge.relation_type !== 'prerequisite_of') continue
    if (edge.from_id === focus.id) related.add(edge.to_id)
    if (edge.to_id === focus.id) related.add(edge.from_id)
  }
  const relationNames = ordered.filter(node => related.has(node.id)).map(node => node.name)
  return { chapterId: focus.chapter_id ?? 'unassigned', chapterName: focus.chapter_name || '其他知识点', chapterNodes, focus, relationNames }
}
