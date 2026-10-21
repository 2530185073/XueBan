import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import examples from '../../../../contracts/phase1/examples.json'
import type { CaseDetail, Decision, Job } from '../core/types'
import type { CurriculumScope } from '../core/learning'
import type { Workspace } from '../useStudentWorkspace'
import { studentResultState, studentUnresolvedKind } from '../presentation'
import { QuestionCard } from './RecognitionResults'
import { ResultsSummary } from './ResultsSummary'

const fixture = <T,>(name: string) => structuredClone(examples.entries.find(entry => entry.name === name)!.value) as T
function sample(reasons = ['provider_unavailable']): CaseDetail {
  const value: CaseDetail = { case: fixture('resolved_case'), question: fixture('question_revision_2'), attempt: fixture('correction_across_question_revision') }
  const decision = fixture<Decision>('model_candidate')
  Object.assign(decision, {
    origin: 'model', validity: 'needs_review', verdict: 'unable_to_judge',
    input_versions: { ...decision.input_versions, question_id: value.case.question_id, question_revision: value.case.current_question_revision, attempt_id: value.case.current_attempt_id, attempt_recognition_revision: value.attempt.recognition_revision },
    verification: { mode: 'two_model', status: 'degraded', independent_readback: false, reason_codes: reasons },
    uncertainties: ['private provider response: do not expose'],
  })
  value.case.current_decision = decision
  value.question.subject = null
  value.attempt.answer_status = 'present'
  value.attempt.answer_text = '－1\n2/7'
  value.attempt.recognition_status = 'needs_revision'
  return value
}
const subjects = [{ subject: '数学', publisher_name: '人民教育出版社' }] as CurriculumScope[]
const workspace = { busy: '', online: true, hint: null, client: {}, recordVersion: 0 } as unknown as Workspace
const card = (row: CaseDetail) => renderToStaticMarkup(<QuestionCard row={row} index={0} open onToggle={() => {}} subjects={subjects} workspace={workspace} onCorrection={() => {}} />)
const summary = (rows: CaseDetail[], compact: boolean) => renderToStaticMarkup(<ResultsSummary rows={rows} detail={null} compact={compact} />)

describe('incomplete service is not student handwriting confirmation', () => {
  it.each(['provider_unavailable', 'reconciliation_unavailable'])('labels %s transcription as a draft without asking the student to choose a subject', reason => {
    const value = sample([reason]), before = structuredClone(value)
    const html = card(value)
    expect(studentResultState(value)).toBe('uncertain') // presentation does not reclassify the stored verdict
    expect(studentUnresolvedKind(value)).toBe('service')
    expect(html).toContain('AI 识读草稿')
    expect(html).toContain('<p>－1\n2/7</p>') // preserve the draft verbatim; do not repair its sign
    expect(html).toContain('可能与照片不同，请以原图为准')
    expect(html).toContain('AI 核对暂未完成')
    expect(html).not.toMatch(/我的作答|无法确定学科|选择题目学科|正确答案<|private provider/)
    for (const control of ['我已在纸上订正', '从相册选择订正照片', '订正记录', '文字和照片不一样']) expect(html).toContain(control)
    expect(value).toEqual(before)
  })
  it('does not assert the student left a blank from an incomplete read', () => {
    const value = sample(); value.attempt.answer_status = 'blank'; value.attempt.answer_text = null
    const html = card(value)
    expect(html).toContain('AI 暂未读到作答')
    expect(html).not.toContain('未填写作答')
  })
  it.each(['visual_unreadable', 'answer_disagreement'])('keeps subject selection available for a non-service unresolved %s result', reason => {
    const value = sample([reason])
    const html = card(value)
    expect(html).toContain('选择题目学科')
    expect(html).toContain('无法确定学科')
    expect(html).not.toContain('AI 识读草稿')
    expect(html).toContain(reason === 'visual_unreadable' ? '这处笔迹暂时看不清，可补拍' : 'AI 还没确认这题')
  })
  it('does not invent a failed service from subject=null without a decision', () => {
    const value = sample(); value.case.current_decision = null
    expect(studentUnresolvedKind(value)).toBeNull()
    expect(card(value)).toContain('选择题目学科')
  })
  it.each(['staff', 'model'] as const)('does not override a current usable %s result with old service reasons', origin => {
    const value = sample(), decision = value.case.current_decision!
    Object.assign(decision, { origin, validity: origin === 'staff' ? 'confirmed' : 'candidate', verdict: 'correct', answer_checks: [] })
    const html = card(value)
    expect(studentUnresolvedKind(value)).toBeNull()
    expect(html).not.toMatch(/AI 识读草稿|AI 核对暂未完成/)
    expect(html).toContain(origin === 'staff' ? '老师确认正确' : 'AI 初判正确')
  })
  it.each(['stale', 'retracted', 'different_attempt', 'different_revision', 'inactive'] as const)('ignores noncurrent %s service reasons', change => {
    const value = sample(), decision = value.case.current_decision!
    if (change === 'stale' || change === 'retracted') decision.validity = change
    if (change === 'different_attempt') decision.input_versions.attempt_id = 'attempt_older'
    if (change === 'different_revision') value.attempt.recognition_revision += 1
    if (change === 'inactive') value.case.lifecycle = 'superseded'
    expect(studentUnresolvedKind(value)).toBeNull()
    expect(card(value)).not.toContain('AI 识读草稿')
  })
})

describe('student summary distinguishes unfinished AI checks', () => {
  it.each([true, false])('does not call 31 service failures 31 handwriting confirmations (compact=%s)', compact => {
    const rows = Array.from({ length: 31 }, () => sample())
    const html = summary(rows, compact)
    expect(html).toContain('本次读到 31 题')
    expect(html).toContain('AI 核对尚未完成 31 道')
    expect(html).toContain('AI 初判正确 0 道')
    expect(html).not.toMatch(/待确认 31|待核实 31|笔迹待核对|无法确定学科|正在/)
    expect(html).toContain('这些题暂不判对错')
  })
  it('counts service, handwriting and other uncertainty separately without inflating pending work', () => {
    const mixed = sample(['provider_unavailable', 'visual_unreadable'])
    const processing = sample(); processing.checkJob = { status: 'running' } as Job
    const correct = sample(); Object.assign(correct.case.current_decision!, { validity: 'candidate', verdict: 'correct' })
    const incorrect = sample(); Object.assign(incorrect.case.current_decision!, { validity: 'candidate', verdict: 'incorrect' })
    const html = summary([sample(), mixed, sample(['visual_unreadable']), sample(['answer_disagreement']), processing, correct, incorrect], true)
    for (const text of ['本次读到 7 题', 'AI 核对尚未完成 2 道', '笔迹待核对 1 道', '待确认 1 道', '处理中 1 道', 'AI 初判正确 1 道', '需订正 1 道']) expect(html).toContain(text)
    expect(studentUnresolvedKind(processing)).toBeNull()
    expect(card(processing)).toContain('正在检查')
    expect(html).not.toContain('待确认 4')
  })
})
