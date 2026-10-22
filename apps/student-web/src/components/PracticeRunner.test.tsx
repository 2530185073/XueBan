import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import type { PracticeAssessment, PracticeItem } from '../core'
import { AnswerInput, buildPracticeSubmission, PracticeItemCard, restoredPracticeInput, practiceSubmissionReady, type PracticeInputValue } from './PracticeRunner'

const base: PracticeItem = { id: 'synthetic-item', knowledge_id: null, graph_version: 'synthetic-book', answer_kind: 'text', stem_text: '化简 2(x+1)，写出过程。', question_type: 'calculation', question_kind: 'solution', has_hint: true, verification: { source: 'ai', status: 'verified', heterogeneous_models: true } }
const blank: PracticeInputValue = { text: '', selected: [], fields: {} }
const render = (item: PracticeItem) => renderToStaticMarkup(<AnswerInput item={item} value={blank} disabled={false} onChange={() => {}} />)

describe('question-specific tablet answers', () => {
  it('renders complete real options, with multiple-choice checkboxes rather than hard-coded A–D', () => {
    const item: PracticeItem = { ...base, answer_kind: 'choice', question_kind: 'choice', question_type: 'multiple_choice', options: [{ label: 'A', text: '-2' }, { label: 'B', text: '3/4' }, { label: 'E', text: 'x^2' }] }
    const html = render(item)
    expect(html.match(/type="checkbox"/g)).toHaveLength(3); expect(html).toContain('3/4'); expect(html).toContain('x^2'); expect(html).not.toContain('type="radio"')
    expect(buildPracticeSubmission(item, { ...blank, selected: ['E', 'A', 'not-an-option'] })).toEqual({ answer_text: 'A, E', selected_options: ['A', 'E'] })
  })
  it('keeps blank identity and order, and does not submit an unfinished field', () => {
    const item: PracticeItem = { ...base, question_kind: 'fill_blank', question_type: 'fill_blank', answer_fields: [{ id: 'area', label: '面积' }, { id: 'perimeter', label: '周长' }] }
    const html = render(item)
    expect(html).toContain('逐空填写'); expect(html).toContain('面积'); expect(html).toContain('周长'); expect(html.match(/<input/g)).toHaveLength(2)
    const incomplete = buildPracticeSubmission(item, { ...blank, fields: { area: 'a^2' } })
    expect(practiceSubmissionReady(incomplete)).toBe(false)
    const complete = buildPracticeSubmission(item, { ...blank, fields: { perimeter: ' 4a ', area: ' a^2 ' } })
    expect(complete.answers).toEqual([{ field_id: 'area', answer_text: 'a^2' }, { field_id: 'perimeter', answer_text: '4a' }]); expect(practiceSubmissionReady(complete)).toBe(true)
  })
  it('provides multiline solution input supporting negative values/fractions and up to 4000 characters', () => {
    const html = render(base)
    expect(html).toContain('<textarea'); expect(html).toContain('maxLength="4000"'); expect(html).toContain('你的解答和过程'); expect(html).not.toContain('手写')
    const input = { ...blank, text: ' 2(x+1)\n=2x+2 ' }
    expect(buildPracticeSubmission(base, input)).toEqual({ answer_text: '2(x+1)\n=2x+2' })
    expect(practiceSubmissionReady({ answer_text: 'x'.repeat(4001) })).toBe(false)
  })
  it('shows AI verification honestly, with answers/solutions hidden until submission', () => {
    const html = renderToStaticMarkup(<PracticeItemCard item={base} index={0} active hintsAllowed busy={false} onAnswer={async () => false} onHint={() => {}} />)
    expect(html).toContain('AI 出题 · 已独立核验'); expect(html).not.toContain('老师已核验'); expect(html).not.toContain('解析')
  })
})

const assessment: PracticeAssessment = { assessment_id: 'assessment_one', student_id: 'student_one', session_id: 'session_one', item_id: base.id, status: 'grading', result: null, feedback: '正在核验本次作答。', solution_text: null, retryable: false, submission: { answer_text: '2(x+1)\n=2x+2', selected_options: null, answers: null } }
const renderAssessment = (value: PracticeAssessment) => renderToStaticMarkup(<PracticeItemCard item={{ ...base, assessment: value }} index={0} active hintsAllowed busy={false} onAnswer={async () => false} onHint={() => {}} />)

describe('asynchronous answer presentation', () => {
  it('restores the submitted solution on refresh and disables paid re-submission while pending', () => {
    const html = renderAssessment(assessment)
    expect(html).toContain('正在核对解法'); expect(html).toContain('刷新后会继续查看进度')
    expect(html).toContain('2(x+1)\n=2x+2'); expect(html).toMatch(/<textarea[^>]*disabled=""/)
    expect(html).toMatch(/<button type="submit"[^>]*disabled=""/)
    expect(html).not.toContain('做对了'); expect(html).not.toContain('解析')
  })
  it('shows a failure as unscored, retains input and only offers an explicit retry', () => {
    const html = renderAssessment({ ...assessment, status: 'failed', result: 'unscorable', retryable: true, feedback: '本次暂未完成核验。' })
    expect(html).toContain('尚未记成对错'); expect(html).toContain('重试核验')
    expect(html).toContain('2(x+1)\n=2x+2'); expect(html).not.toMatch(/<button type="submit"[^>]*disabled=""/)
    expect(html).not.toContain('正在核对解法'); expect(html).not.toContain('这次没对上')
  })
  it('keeps superseded and completed-without-response attempts locked instead of resubmitting', () => {
    const superseded = renderAssessment({ ...assessment, status: 'superseded', result: 'unscorable' })
    expect(superseded).toContain('这次核验不计入结果'); expect(superseded).toMatch(/<button type="submit"[^>]*disabled=""/)
    const completed = renderAssessment({ ...assessment, status: 'completed', result: 'correct', solution_text: '2x+2' })
    expect(completed).toContain('正在读取结果'); expect(completed).not.toContain('做对了')
    expect(completed).toMatch(/<button type="submit"[^>]*disabled=""/)
  })
  it('restores each blank using its field id, without parsing a flattened answer string', () => {
    const item = { ...base, assessment: { ...assessment, submission: { answer_text: '面积：a^2\n周长：4a', answers: [{ field_id: 'perimeter', answer_text: '4a' }, { field_id: 'area', answer_text: 'a^2' }] } } }
    expect(restoredPracticeInput(item).fields).toEqual({ perimeter: '4a', area: 'a^2' })
  })
})
