import { describe, expect, it, vi } from 'vitest'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { KnowledgeReviewNotice, withLinkVersions } from './KnowledgeLinkEditor'
import { isKnowledgeLinkList, type LinkDecision } from '../api/learningTypes'
import { LearningApi } from '../api/learning'
import type { StaffApi } from '../api/client'

describe('knowledge link snapshot binding', () => {
  const snapshot = { question_id: 'question_1', question_revision: 3, graph_version: 'graph-2026', items: [] }
  const choice = { knowledge_id: 'kp-1', role: 'primary', status: 'confirmed', expected_link_id: null, rationale: null } as const
  it('sends the question and graph revisions actually read, including reject/replace choices', () => {
    expect(withLinkVersions(snapshot, choice)).toEqual({ ...choice, expected_question_revision: 3, expected_graph_version: 'graph-2026' })
    expect(withLinkVersions(snapshot, { ...choice, status: 'rejected' }).expected_question_revision).toBe(3)
  })
  it('reads the question-specific graph without reusing another student graph cache', () => {
    const call = vi.fn(() => Promise.resolve({})), api = new LearningApi({ call } as unknown as StaffApi)
    api.graph('question_1'); api.graph('question_2')
    expect(call.mock.calls.map((args: unknown[]) => args[0])).toEqual(['/v1/curriculum/graph?question_id=question_1', '/v1/curriculum/graph?question_id=question_2'])
  })
  it('requires explicit versions in the read contract and before mutation', () => {
    expect(isKnowledgeLinkList(snapshot)).toBe(true)
    for (const bad of [{ ...snapshot, question_revision: undefined }, { ...snapshot, question_revision: 0 }, { ...snapshot, graph_version: null }]) expect(isKnowledgeLinkList(bad)).toBe(false)
    const call = vi.fn(), api = new LearningApi({ call } as unknown as StaffApi)
    expect(() => api.decideLink('question_1', choice as LinkDecision)).toThrow()
    expect(call).not.toHaveBeenCalled()
    api.decideLink('question_1', withLinkVersions(snapshot, choice))
    expect(call.mock.calls[0][2].payload).toEqual(withLinkVersions(snapshot, choice))
  })
})

describe('review handoff to knowledge confirmation', () => {
  const link = { id: 'link_1', question_id: 'question_1', knowledge_id: 'kp-1', knowledge_name: '有理数', role: 'primary', source: 'model', status: 'confirmed', rationale: null, actor_id: null, supersedes_id: null, created_at: '2026-10-26T01:00:00Z' } as const
  it('shows the missing step after a teacher judgment even if an AI link exists', () => {
    const html = renderToStaticMarkup(createElement(KnowledgeReviewNotice, { judgmentConfirmed: true, items: [link] }))
    expect(html).toContain('下一步：确认本题的主要知识点')
    expect(html).not.toContain('对错与主要知识点均已确认')
  })
  it('only marks both steps complete for a staff-confirmed primary', () => {
    const render = (items: typeof link[] | Array<Omit<typeof link, 'source' | 'role'> & { source: string; role: 'primary' | 'secondary' }>) => renderToStaticMarkup(createElement(KnowledgeReviewNotice, { judgmentConfirmed: true, items }))
    expect(render([{ ...link, source: 'staff', role: 'secondary' }])).toContain('下一步')
    expect(render([{ ...link, source: 'staff' }])).toContain('对错与主要知识点均已确认')
    expect(renderToStaticMarkup(createElement(KnowledgeReviewNotice, { judgmentConfirmed: false, items: [] }))).toBe('')
  })
})
