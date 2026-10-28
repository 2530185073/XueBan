import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { KnowledgeMapView, SubjectFeedbackNote } from './components/KnowledgeMap'
import type { CurriculumSubject } from './core/learning'
import { isKnowledgeMap } from './core/learning'
import { graphMap } from './core/knowledge.fixtures'

const noop = () => {}
const item = {
  response_id: 'response-first', session_id: 'session-first', item_id: 'item-1',
  case_id: 'case-1', homework_id: 'homework-1', kind: 'first_unassisted', result: 'incorrect',
  hint_used: false, occurred_at: '2026-10-28T06:25:00Z', stem_preview: '3÷(-3/4)等于几？',
}
const subject = {
  grade: 7, subject: '数学', publisher_code: 'BNUP', publisher_name: '北京师范大学出版社', stage: 'junior_middle',
  revision_policy: 'publisher_latest', textbook_revision: 'publisher_latest', catalog_status: 'available',
  catalog_verified_at: null, catalog_candidates: [], books: [], graph_ready: false, status: 'building',
  correct_count: 8, incorrect_count: 2, partial_count: 1, scorable_count: 10, score_percent: 85,
  homework_feedback: { model_count: 10, staff_count: 0, pending_count: 1 },
  practice_feedback: {
    total: 2, first_unassisted_count: 1, first_unassisted_correct: 0, first_unassisted_incorrect: 1,
    assisted_count: 0, retry_count: 1, truncated: false,
    items: [{ ...item, response_id: 'response-retry', session_id: 'session-retry', kind: 'retry', result: 'correct' }, item],
  },
} satisfies CurriculumSubject & { homework_feedback: unknown; practice_feedback: unknown }

describe('honest feedback without a reviewed textbook graph', () => {
  it('shows real subject samples and retry history without graph nodes or a mastery claim', () => {
    const map = { ...graphMap, graph_version: null, graph_review_status: 'not_ready_latest_edition',
      graph_unavailable_reason: 'no_reviewed_graph', nodes: [], edges: [], states: [], subjects: [subject],
      projection: { pending: true, projected_at: null, evidence_revision: 0, state_version: 0 } }
    expect(isKnowledgeMap(map)).toBe(true)
    const html = renderToStaticMarkup(<KnowledgeMapView map={map} onOpen={noop} onOpenHomework={noop} />)
    for (const text of ['数学', '10 道有可用判定', '正确 8 道', '部分正确 1 道', '错误 1 道', '样本参考分 85',
      'AI 初判 10 道', '另有 1 道待核实', '当前年级与教材范围内没有已完成审核的知识图谱',
      '首次且未记录提示或答案帮助 1 次（正确 0 次，错误 1 次）', '同题重做 1 次',
      '本次做对', '本次仍需练习', '不直接升级为掌握', '回看来源原题']) expect(html).toContain(text)
    for (const text of ['正在整理你的学习足迹', '正在整理新的作答记录', '阶段性较稳', '认识有理数', '展示模式']) expect(html).not.toContain(text)
  })

  it('keeps a viewed-answer success separate from a first answer without recorded help', () => {
    const viewed = { ...subject, practice_feedback: { ...subject.practice_feedback,
      total: 1, first_unassisted_count: 0, first_unassisted_incorrect: 0, assisted_count: 1, retry_count: 0,
      items: [{ ...item, kind: 'assisted', result: 'correct', hint_used: true }],
    } }
    const html = renderToStaticMarkup(<SubjectFeedbackNote subjects={[viewed]} />)
    expect(html).toContain('帮助后首次作答 1 次')
    expect(html).toContain('提示或答案后作答')
    expect(html).toContain('未记录帮助不等于已确认独立完成')
    expect(html).not.toContain('回看来源原题')
  })

  it('accepts older maps while not inventing practice records or teacher provenance', () => {
    const { homework_feedback: _homework, practice_feedback: _practice, ...oldSubject } = subject
    const html = renderToStaticMarkup(<SubjectFeedbackNote subjects={[oldSubject]} />)
    expect(html).toContain('10 道有可用判定')
    expect(html).not.toContain('已核验变式作答')
    expect(html).not.toContain('老师核实 0 道')
  })

  it.each([
    { total: 3 }, { retry_count: -1 }, { first_unassisted_correct: 1 }, { truncated: true },
    { items: [{ ...item, kind: 'independently_mastered' }, item] },
    { items: [item, item] }, { items: [{ ...item, homework_id: '' }, item] },
    { items: [{ ...item, occurred_at: 'bad-date' }, item] },
  ])('hides malformed optional feedback rather than showing unsupported conclusions: %j', patch => {
    const malformed = { ...subject, practice_feedback: { ...subject.practice_feedback, ...patch } }
    const html = renderToStaticMarkup(<SubjectFeedbackNote subjects={[malformed]} />)
    expect(html).toContain('10 道有可用判定')
    expect(html).not.toContain('已核验变式作答')
  })

  it('does not manufacture a score or practice activity for a learner without observations', () => {
    const empty = { ...subject, correct_count: 0, incorrect_count: 0, partial_count: 0, scorable_count: 0, score_percent: null,
      homework_feedback: { model_count: 0, staff_count: 0, pending_count: 0 },
      practice_feedback: { total: 0, first_unassisted_count: 0, first_unassisted_correct: 0,
        first_unassisted_incorrect: 0, assisted_count: 0, retry_count: 0, items: [], truncated: false } }
    expect(renderToStaticMarkup(<SubjectFeedbackNote subjects={[empty]} />)).toBe('')
  })
})
