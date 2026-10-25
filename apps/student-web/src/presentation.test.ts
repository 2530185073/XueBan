import { describe, expect, it } from 'vitest'
import examples from '../../../contracts/phase1/examples.json'
import type { Attempt, CapturedQuestionRevision, CaseDetail, CorrectionCase, Decision, DraftView, Job } from './core/types'
import { casePresentation, correctionTarget, dateLabel, draftProgress, preferredCase, replacementTarget, shouldObserveDraft, stageLabels, studentQuestionText, studentResultState, studentResultUncertain } from './presentation'

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
  it.each([
    ['3x + 1 = 10', '3'], ['12 + 8 = ?', '1'], ['0.5 + 1 = ?', '4'],
    ['3.14 × 2', '3'], ['3² + 1', '3'], ['3(1)+2', '3(1)'],
    ['3 - 2 = ?', '3'], ['3 x + 1 = 10', '3'], ['一元一次方程', '一'],
    ['0.5 + 1 = ?', undefined],
  ])('preserves mathematical content %s when a label is ambiguous', (stem, label) => {
    expect(studentQuestionText(stem!, label)).toBe(stem)
  })
  it('strips only clearly separated explicit labels', () => {
    expect(studentQuestionText('3、计算 3x + 1。', '3')).toBe('计算 3x + 1。')
    expect(studentQuestionText('(1) 计算 0.5 + 1。', '(1)')).toBe('计算 0.5 + 1。')
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
  it.each([
    ['provider_unavailable', 'AI 核对暂未完成'],
    ['reconciliation_unavailable', 'AI 核对暂未完成'],
    ['visual_unreadable', '这处笔迹暂时看不清，可补拍'],
    ['stem_disagreement', 'AI 还没确认这题'],
  ])('distinguishes unresolved %s without claiming an active task or teacher handoff', (reason, label) => {
    const row = sample(), decision = row.case.current_decision!
    decision.validity = 'needs_review'; decision.verdict = 'unable_to_judge'
    decision.verification = { mode: 'two_model', status: 'conflict', independent_readback: true, reason_codes: [reason] }
    row.attempt.recognition_status = 'needs_revision'
    row.checkJob = fixture<Job>('successful_check_job')
    expect(studentResultState(row)).toBe('uncertain')
    expect(casePresentation(row).label).toBe(label)
    expect(casePresentation(row).note).not.toMatch(/正在|老师/)
  })
  it.each(['queued', 'running', 'retryable_failed'] as const)('shows an actual %s check ahead of an older completed decision', status => {
    const row = sample(), job = fixture<Job>('successful_check_job')
    job.status = status; row.checkJob = job
    expect(studentResultState(row)).toBe(status === 'running' ? 'processing' : 'unprocessed')
    expect(casePresentation(row).label).toBe({ queued: '排队等待处理', running: '正在检查', retryable_failed: '等待服务重试' }[status])
    row.case.current_decision!.validity = 'needs_review'
    expect(studentResultState(row)).toBe(status === 'running' ? 'processing' : 'unprocessed')
    expect(casePresentation(row).label).toBe({ queued: '排队等待处理', running: '正在检查', retryable_failed: '等待服务重试' }[status])
  })
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

describe('explicit unanswered state', () => {
  const blank = () => {
    const value = sample()
    value.attempt.answer_status = 'blank'; value.attempt.answer_text = null
    value.attempt.recognition_status = 'needs_revision'
    value.case.current_decision!.validity = 'needs_review'
    value.case.current_decision!.verdict = 'unable_to_judge'
    value.question.recognition_uncertainties = ['题目右侧没有拍全']
    return value
  }
  it('shows an explicit blank as unanswered even when the question itself is incomplete', () => {
    const value = blank(), before = structuredClone(value)
    expect(studentResultState(value)).toBe('unanswered')
    expect(studentResultUncertain(value)).toBe(false)
    expect(casePresentation(value)).toMatchObject({ label: '未作答', tone: 'neutral' })
    expect(value).toEqual(before)
  })
  it('accepts a current usable unanswered verdict without inferring it from missing text', () => {
    const value = sample()
    value.case.current_decision!.verdict = 'unanswered'
    expect(studentResultState(value)).toBe('unanswered')
  })
  it.each(['present', 'unclear', undefined] as const)('does not treat missing text with status %s as unanswered', status => {
    const value = blank(); value.attempt.answer_status = status
    expect(studentResultState(value)).toBe('uncertain')
  })
  it.each(['running', 'queued', 'retryable_failed'] as const)('keeps %s work distinct from the previous blank readback', status => {
    const value = blank(); value.checkJob = { status } as Job
    expect(studentResultState(value)).toBe(status === 'running' ? 'processing' : 'unprocessed')
  })
  it.each(['pending', 'failed'] as const)('does not reuse a blank while recognition is %s', status => {
    const value = blank(); value.case.current_decision = null; value.attempt.recognition_status = status
    expect(studentResultState(value)).toBe(status === 'pending' ? 'processing' : 'service_failure')
  })
  it.each(['old_attempt', 'new_question', 'inactive', 'stale_verdict'] as const)('ignores obsolete blank evidence: %s', kind => {
    const value = blank(); value.case.current_decision = null
    if (kind === 'old_attempt') value.case.current_attempt_id = 'new_attempt'
    if (kind === 'new_question') value.case.current_question_revision += 1
    if (kind === 'inactive') value.case.lifecycle = 'superseded'
    if (kind === 'stale_verdict') {
      value.attempt.answer_status = 'present'
      value.case.current_decision = sample().case.current_decision
      value.case.current_decision!.verdict = 'unanswered'; value.case.current_decision!.validity = 'stale'
    }
    expect(studentResultState(value)).toBe('unprocessed')
  })
})

describe('bounded job observation presentation', () => {
  const draft = (status: 'queued' | 'running' | 'retryable_failed', paused = false) => ({ stage: 'waiting', observation: { status, paused, phase: 'recognizing', lastProgressAt: 0, jobs: {} } }) as DraftView
  it('distinguishes queued, running, and retry backoff without claiming queued work is reading', () => {
    expect(draftProgress(draft('queued'))).toMatchObject({ label: '排队等待处理', spinning: false })
    expect(draftProgress(draft('running'))).toMatchObject({ label: '读题与作答', spinning: true })
    const check = draft('running'); check.observation!.phase = 'checking'
    expect(draftProgress(check).label).toBe('逐题检查')
    expect(draftProgress(draft('retryable_failed'))).toMatchObject({ label: '等待服务重试', spinning: false })
  })
  it('pauses the whole draft and running cards without discarding existing decisions', () => {
    const paused = draft('running', true), row = sample()
    row.checkJob = { ...fixture<Job>('successful_check_job'), status: 'running' }; row.observationPaused = true
    expect(draftProgress(paused)).toMatchObject({ label: '进度已保留', spinning: false })
    expect(draftProgress(paused).note).toContain('服务可能暂不可用')
    expect(shouldObserveDraft(paused)).toBe(false)
    expect(studentResultState(row)).toBe('unprocessed')
    expect(casePresentation(row).label).toBe('进度已保留')
    expect(row.case.current_decision).not.toBeNull()
    expect(shouldObserveDraft(draft('queued'))).toBe(true)
  })
  it('observes legacy drafts once but does not auto-run missing checks without an active job', () => {
    const old = { stage: 'recognizing' } as DraftView
    expect(shouldObserveDraft(old)).toBe(true)
    expect(draftProgress(old).spinning).toBe(false)
    const noJob = draft('queued'); noJob.observation!.status = null
    expect(shouldObserveDraft(noJob)).toBe(false)
  })
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
