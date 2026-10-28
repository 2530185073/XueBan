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
const card = (row: CaseDetail) => {
  const before = structuredClone(row), decision = row.case.current_decision
  const html = renderToStaticMarkup(<QuestionCard row={row} index={0} open onToggle={() => {}} subjects={subjects} workspace={workspace} onCorrection={() => {}} />)
  expect(row).toEqual(before)
  expect(row.case.current_decision).toBe(decision)
  expect(html).not.toMatch(/question-detail|hint-section|help-panel|case-history|查看答案与解法|选做一道同题型/)
  expect(html.match(/<button\b/g)).toHaveLength(1)
  return html
}
const summary = (rows: CaseDetail[], compact: boolean) => {
  const before = structuredClone(rows), decisions = rows.map(row => row.case.current_decision)
  const html = renderToStaticMarkup(<ResultsSummary rows={rows} detail={null} compact={compact} />)
  expect(rows).toEqual(before)
  rows.forEach((row, index) => expect(row.case.current_decision).toBe(decisions[index]))
  return html
}

describe('incomplete service is not student handwriting confirmation', () => {
  it.each(['provider_unavailable', 'reconciliation_unavailable'])('keeps %s transcription neutral in a simple card', reason => {
    const value = sample([reason]), before = structuredClone(value)
    const html = card(value)
    expect(studentResultState(value)).toBe('uncertain') // presentation does not reclassify the stored verdict
    expect(studentUnresolvedKind(value)).toBe('service')
    expect(html).toContain('<p>－1\n2/7</p>') // preserve the draft verbatim; do not repair its sign
    expect(html).toContain('class="recognition-status uncertain"')
    expect(html).toContain('class="recognition-answer uncertain"')
    expect(html).not.toMatch(/正确答案<|正确解题步骤|private provider|class="recognition-answer (correct|incorrect)"|✓ 正确|✕ 错误/)
    for (const control of ['我已在纸上订正', '从相册选择订正照片', '订正记录', '文字和照片不一样']) expect(html).not.toContain(control)
    expect(value).toEqual(before)
  })
  it('does not treat an unfinished service readback as a confirmed blank in the card or summary', () => {
    const value = sample(); value.attempt.answer_status = 'blank'; value.attempt.answer_text = null
    const html = card(value)
    expect(studentUnresolvedKind(value)).toBe('service')
    expect(html).toContain('class="recognition-status uncertain"')
    expect(html).toContain('class="recognition-answer uncertain"')
    expect(html).toContain('</svg>未完成</span>')
    expect(html).toContain('<span class="recognition-label">识别内容</span>')
    expect(html).toContain('<p>暂未读清作答</p>')
    expect(html).toContain('核对尚未完成，原图已保留。')
    expect(html).not.toMatch(/未作答|未填写作答|正确答案<|正确解题步骤|✓ 正确|✕ 错误|class="recognition-(?:status|answer) (correct|incorrect|unanswered)"/)
    const compact = summary([value], true)
    expect(compact).toContain('本次作答 1 题 · 正确 0 道 · 错误 0 道')
    expect(compact).toContain('AI 核对尚未完成 1 道')
    expect(compact).not.toContain('未作答')
  })
  it('does not infer a blank or an incorrect answer from missing transcription alone', () => {
    const value = sample(); value.attempt.answer_text = null
    const html = card(value)
    expect(html).toContain('class="recognition-status uncertain"')
    expect(html).toContain('class="recognition-answer uncertain"')
    expect(html).not.toMatch(/未填写作答|class="recognition-status unanswered"|正确答案<|正确解题步骤|✓ 正确|✕ 错误/)
  })
  it.each(['visual_unreadable', 'answer_disagreement'])('keeps subject selection available for a non-service unresolved %s result', reason => {
    const value = sample([reason])
    const html = card(value)
    expect(html).toContain('选择题目学科')
    expect(html).toContain('无法确定学科')
    expect(html).not.toContain('AI 识读草稿')
    expect(studentUnresolvedKind(value)).toBe(reason === 'visual_unreadable' ? 'handwriting' : 'other')
    expect(html).toContain('class="recognition-status uncertain"')
    expect(html).not.toMatch(/正确答案<|正确解题步骤|✓ 正确|✕ 错误/)
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
    expect(html).toContain('</svg>正确</span>')
    expect(html).not.toMatch(/老师确认正确|AI 初判正确|正确答案<|正确解题步骤/)
  })
  it.each(['stale', 'retracted', 'different_attempt', 'different_revision', 'inactive'] as const)('ignores noncurrent %s service reasons', change => {
    const value = sample(), decision = value.case.current_decision!
    if (change === 'stale' || change === 'retracted') decision.validity = change
    if (change === 'different_attempt') decision.input_versions.attempt_id = 'attempt_older'
    if (change === 'different_revision') value.attempt.recognition_revision += 1
    if (change === 'inactive') value.case.lifecycle = 'superseded'
    expect(studentUnresolvedKind(value)).toBeNull()
    const html = card(value)
    expect(html).toContain('尚未检查')
    expect(html).not.toMatch(/AI 识读草稿|正确答案<|正确解题步骤|✓ 正确|✕ 错误/)
  })
})

describe('student summary distinguishes unfinished AI checks', () => {
  it.each([true, false])('does not call 31 service failures 31 handwriting confirmations (compact=%s)', compact => {
    const rows = Array.from({ length: 31 }, () => sample())
    const html = summary(rows, compact)
    if (compact) {
      expect(html).toContain('本次作答 31 题 · 正确 0 道 · 错误 0 道')
      expect(html).toMatch(/(?:待确认|AI 核对尚未完成) 31 道/)
      expect(html).not.toMatch(/AI 初判|老师确认|需订正|正确率|%/)
    } else {
      expect(html).toContain('本次读到 31 题')
      expect(html).toContain('AI 核对尚未完成 31 道')
      expect(html).toContain('AI 初判正确 0 道')
      expect(html).toContain('这些题暂不判对错')
    }
    expect(html).not.toMatch(/笔迹待核对|无法确定学科|正在|正确 31|错误 31|需订正 31|全部正确/)
  })
  it('counts service, handwriting and other uncertainty separately without inflating pending work', () => {
    const mixed = sample(['provider_unavailable', 'visual_unreadable'])
    const processing = sample(); processing.checkJob = { status: 'running' } as Job
    const correct = sample(); Object.assign(correct.case.current_decision!, { validity: 'candidate', verdict: 'correct' })
    const incorrect = sample(); Object.assign(incorrect.case.current_decision!, { validity: 'candidate', verdict: 'incorrect' })
    const html = summary([sample(), mixed, sample(['visual_unreadable']), sample(['answer_disagreement']), processing, correct, incorrect], true)
    expect(html).toContain('本次作答 7 题 · 正确 1 道 · 错误 1 道')
    expect(html).toContain('处理中 1 道')
    const unresolvedCounts = Array.from(html.matchAll(/(?:AI 核对尚未完成|笔迹待核对|待确认) (\d+) 道/g), match => Number(match[1]))
    expect(unresolvedCounts.reduce((sum, count) => sum + count, 0)).toBe(4)
    expect(studentUnresolvedKind(processing)).toBeNull()
    expect(card(processing)).toContain('处理中')
    expect(card(processing)).not.toMatch(/正确答案<|正确解题步骤|✓ 正确|✕ 错误/)
    expect(html).not.toMatch(/AI 初判|老师确认|需订正|待确认 5|正确率|%/)
  })
  it('combines model and staff verdicts while keeping unanswered and failed checks separate', () => {
    const correctModel = sample(), correctStaff = sample(), incorrectModel = sample(), incorrectStaff = sample(), unanswered = sample(), failure = sample()
    Object.assign(correctModel.case.current_decision!, { validity: 'candidate', verdict: 'correct' })
    Object.assign(correctStaff.case.current_decision!, { origin: 'staff', validity: 'confirmed', verdict: 'correct' })
    Object.assign(incorrectModel.case.current_decision!, { validity: 'candidate', verdict: 'partially_correct' })
    Object.assign(incorrectStaff.case.current_decision!, { origin: 'staff', validity: 'confirmed', verdict: 'incorrect' })
    Object.assign(unanswered.case.current_decision!, { validity: 'candidate', verdict: 'unanswered' })
    failure.case.current_decision = null; failure.checkJob = { status: 'failed' } as Job
    const html = summary([correctModel, correctStaff, incorrectModel, incorrectStaff, unanswered, failure], true)
    expect(html).toContain('本次作答 5 题 · 正确 2 道 · 错误 2 道')
    expect(html).toContain('未作答 1 道')
    expect(html).toContain('服务失败 1 道')
    expect(html).not.toMatch(/AI 初判|老师确认|需订正|待确认|正确率|%/)
  })
})
