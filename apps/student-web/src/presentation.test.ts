import { describe, expect, it } from 'vitest'
import examples from '../../../contracts/phase1/examples.json'
import type { Attempt, CapturedQuestionRevision, CaseDetail, CorrectionCase, Decision, DraftView, Job } from './core/types'
import { casePresentation, correctionTarget, dateLabel, preferredCase, replacementTarget, stageLabels, studentQuestionText, studentResultState, studentResultUncertain } from './presentation'

const fixture = <T>(name: string) => structuredClone(examples.entries.find(entry => entry.name === name)!.value) as T
const sample = (): CaseDetail => {
  const c = fixture<CorrectionCase>('resolved_case')
  const q = fixture<CapturedQuestionRevision>('question_revision_2')
  const a = fixture<Attempt>('correction_across_question_revision')
  const d = fixture<Decision>('model_candidate')
  Object.assign(c, { id: 'case_test', question_id: 'question_test', current_attempt_id: 'attempt_test', current_question_revision: q.revision, attempt_ids: ['attempt_test'], current_decision: d })
  Object.assign(q, { question_id: 'question_test', homework_id: c.homework_id, label: '1', stem_text: '计算。' })
  Object.assign(a, { id: 'attempt_test', case_id: c.id, question_id: q.question_id, captured_question_revision: q.revision, answer_text: '4', recognition_status: 'ready' })
  Object.assign(d, { case_id: c.id, input_versions: { ...d.input_versions, question_id: q.question_id, attempt_id: a.id, question_revision: q.revision, attempt_recognition_revision: a.recognition_revision } })
  return { case: c, question: q, attempt: a }
}

describe('student-facing evidence states', () => {
  it('uses clean question text without the printed source label', () => {
    expect(studentQuestionText('三、习作：点亮家乡名片。', '三、习作')).toBe('点亮家乡名片。')
    expect(studentQuestionText('3. 短文为了说明古人改错字的方法。', '3')).toBe('短文为了说明古人改错字的方法。')
  })
  it('labels AI candidate as preliminary and staff confirmation separately', () => { const row = sample(); expect(casePresentation(row).label).toBe('初判正确'); row.case.current_decision!.origin = 'staff'; row.case.current_decision!.validity = 'confirmed'; expect(casePresentation(row).label).toBe('老师确认正确') })
  it('keeps a usable verdict when the first pass has a non-blocking uncertainty', () => {
    const row = sample(); row.attempt.recognition_status = 'needs_revision'; row.attempt.recognition_uncertainties = ['涂改影响一行中间步骤的辨认。']
    expect(studentResultState(row)).toBe('correct'); expect(studentResultUncertain(row)).toBe(true)
    expect(casePresentation(row).label).toBe('初判正确')
  })
  it('keeps an incorrect verdict and its uncertainty star when recognition needs revision', () => {
    const row = sample(); row.case.current_decision!.verdict = 'incorrect'; row.attempt.recognition_status = 'needs_revision'
    expect(studentResultState(row)).toBe('incorrect'); expect(studentResultUncertain(row)).toBe(true); expect(casePresentation(row).label).toBe('需要订正')
  })
  it('keeps genuinely unresolved answers out of the right/wrong buckets', () => { const row = sample(); row.case.current_decision!.validity = 'needs_review'; expect(casePresentation(row).tone).toBe('amber'); expect(studentResultState(row)).toBe('uncertain') })
  it('shows a needs-revision answer without a verdict as pending, not as uncertain right or wrong', () => {
    const row = sample(); row.case.current_decision = null; row.attempt.recognition_status = 'needs_revision'
    expect(studentResultState(row)).toBe('unprocessed'); expect(studentResultUncertain(row)).toBe(false); expect(casePresentation(row).label).toBe('识别待核实')
  })
  it('shows recognition service failure separately from pending work', () => {
    const row = sample(); row.case.current_decision = null; row.attempt.recognition_status = 'failed'
    expect(studentResultState(row)).toBe('service_failure'); expect(casePresentation(row).label).toBe('识别服务失败')
  })
  it('shows a failed check service separately from an unread answer', () => {
    const row = sample(); row.case.current_decision = null; row.attempt.recognition_status = 'ready'
    const job = fixture<Job>('successful_check_job'); job.status = 'failed'; row.checkJob = job
    expect(studentResultState(row)).toBe('service_failure'); expect(casePresentation(row).label).toBe('检查服务失败')
  })
  it('shows an active check as processing and an unread answer as processing', () => {
    const checked = sample(); checked.case.current_decision = null; checked.attempt.recognition_status = 'ready'
    const job = fixture<Job>('successful_check_job'); job.status = 'running'; checked.checkJob = job
    expect(studentResultState(checked)).toBe('processing')
    const reading = sample(); reading.case.current_decision = null; reading.attempt.recognition_status = 'pending'
    expect(studentResultState(reading)).toBe('processing')
  })
  it('hides decisions tied to an older answer or input revision', () => { const row = sample(); row.case.current_decision!.input_versions.attempt_id = 'attempt_previous'; expect(casePresentation(row).label).toBe('结果待更新'); const revision = sample(); revision.attempt.recognition_revision = 2; expect(casePresentation(revision).label).toBe('结果待更新'); const other = sample(); other.case.current_decision!.input_versions.question_id = 'question_other'; expect(casePresentation(other).label).toBe('结果待更新') })
  it('does not turn completed processing into a correct verdict', () => { expect(stageLabels.complete).toBe('可查看结果'); const row = sample(); row.case.current_decision = null; expect(casePresentation(row).label).toBe('等待检查') })
  it('renders unavailable dates explicitly', () => { expect(dateLabel('invalid')).toBe('时间待确认') })
})

describe('correction photo intent', () => {
  it('keeps the corrected question selected when a whole page returns', () => {
    const first = sample(), second = sample()
    first.case.id = 'case_first'; second.case.id = 'case_second'
    expect(preferredCase([first, second], null, 'case_second')).toBe(second)
    expect(preferredCase([first, second], 'case_first', 'case_second')).toBe(first)
    expect(preferredCase([first, second], 'old_unrelated_case', 'case_second')).toBe(second)
  })
  it('replacing a correction photo retains the original case and expected parent after restore', () => {
    const row = sample(); row.case.id = 'case_second'; row.attempt.id = 'attempt_before_correction'
    const original = correctionTarget(row)
    const restored = JSON.parse(JSON.stringify({ correction: original })) as DraftView
    expect(replacementTarget(restored)).toEqual({ caseId: 'case_second', parentAttemptId: 'attempt_before_correction' })
    expect(replacementTarget({} as DraftView)).toBeUndefined()
  })
})
