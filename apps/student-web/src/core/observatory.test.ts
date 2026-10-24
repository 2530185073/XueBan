import { describe, expect, it } from 'vitest'
import type { KnowledgeGraphEdge, KnowledgeGraphNode, LearningStatus } from './learning'
import { createObservatoryPlan } from './observatory'

const node = (id: string, chapter: string, order: number, status: LearningStatus, evidence = 0): KnowledgeGraphNode => ({
  id, node_type: 'skill', parent_id: chapter, name: `知识点 ${id}`, definition: null, observable_objective: null,
  sort_order: order, subject: '数学', chapter_id: chapter, chapter_name: `章节 ${chapter}`,
  learning_status: status, reason_codes: [], valid_evidence_count: evidence,
  staff_verified_evidence_count: evidence, model_evidence_count: 0, assessment_source: evidence ? 'staff' : 'none',
})
const edge = (from_id: string, to_id: string): KnowledgeGraphEdge => ({ id: `${from_id}-${to_id}`, from_id, to_id, relation_type: 'prerequisite_of', scope: null, rationale: null })

describe('learning observatory plan', () => {
  it('chooses the chapter with real attention records and its first attention node', () => {
    const nodes = [node('a', 'one', 1, 'stable_for_now', 3), node('b', 'two', 2, 'needs_help_now', 2), node('c', 'two', 3, 'consolidating', 1)]
    const plan = createObservatoryPlan(nodes, [edge('a', 'b')])
    expect(plan?.chapterId).toBe('two')
    expect(plan?.focus.id).toBe('b')
    expect(plan?.relationNames).toEqual(['知识点 a'])
  })
  it('does not invent a weakness when there are no attention records', () => {
    const plan = createObservatoryPlan([node('a', 'one', 1, 'stable_for_now', 3), node('b', 'one', 2, 'no_judgment')], [])
    expect(plan?.focus.id).toBe('a')
    expect(plan?.focus.learning_status).toBe('stable_for_now')
  })
  it('is deterministic and handles an empty graph', () => {
    const nodes = [node('a', 'one', 1, 'consolidating', 1), node('b', 'one', 2, 'no_judgment')]
    expect(createObservatoryPlan(nodes, [])).toEqual(createObservatoryPlan([...nodes].reverse(), []))
    expect(createObservatoryPlan([], [])).toBeNull()
  })
})
