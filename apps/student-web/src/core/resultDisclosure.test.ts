import { describe, expect, it, vi } from 'vitest'
import type { CaseDetail } from './types'
import { prepareResultCardOpen, resultCardBinding, resultCardShowsSolution } from './resultDisclosure'

const row = (): CaseDetail => ({
  case: { id: 'case_one', lifecycle: 'active', progress: 'needs_correction', question_id: 'question_one',
    current_question_revision: 1, current_attempt_id: 'attempt_one', current_decision: {
      id: 'decision_one', revision: 1, origin: 'model', validity: 'candidate', verdict: 'incorrect',
      correct_answer: '2', correction_method: '1 + 1 = 2',
      input_versions: { question_id: 'question_one', question_revision: 1, attempt_id: 'attempt_one', attempt_recognition_revision: 1 },
    } },
  question: { question_id: 'question_one', revision: 1, stem_text: '1 + 1 = ?' },
  attempt: { id: 'attempt_one', revision: 1, recognition_revision: 1, recognition_status: 'ready', answer_status: 'present', answer_text: '3' },
} as CaseDetail)

describe('single-click result disclosure', () => {
  it('records a wrong answer solution before opening, without altering its result', async () => {
    const value = row(), before = structuredClone(value), record = vi.fn(async () => {})
    expect(await prepareResultCardOpen(value, record, () => true)).toBe(true)
    expect(record).toHaveBeenCalledExactlyOnceWith(value)
    expect(value).toEqual(before)
  })
  it.each(['correct', 'unanswered', 'unable_to_judge'] as const)('opens %s without falsely recording answer help', async verdict => {
    const value = row(); value.case.current_decision!.verdict = verdict
    const record = vi.fn(async () => {})
    expect(await prepareResultCardOpen(value, record, () => true)).toBe(true)
    expect(record).not.toHaveBeenCalled()
  })
  it('does not expose stale or running judgments', () => {
    const value = row(); value.case.current_decision!.validity = 'stale'
    expect(resultCardShowsSolution(value)).toBe(false)
    value.case.current_decision!.validity = 'candidate'
    value.checkJob = { status: 'running' } as CaseDetail['checkJob']
    expect(resultCardShowsSolution(value)).toBe(false)
  })
  it.each(['editing', 'rechecking'] as const)('does not record or reveal persisted %s results without a checkJob', progress => {
    const value = row(); value.case.progress = progress
    expect(resultCardShowsSolution(value)).toBe(false)
  })
  it('records a visible per-blank answer even if the overall verdict is correct', async () => {
    const value = row(); value.case.current_decision!.verdict = 'correct'
    value.case.current_decision!.answer_checks = [{ index: 1, prompt: '第一空', student_answer: '3', verdict: 'incorrect', correct_answer: '2', explanation: '1 + 1 = 2' }]
    const record = vi.fn(async () => {})
    expect(await prepareResultCardOpen(value, record, () => true)).toBe(true)
    expect(record).toHaveBeenCalledOnce()
  })
  it('does not write if navigation or identity changed before the click is processed', async () => {
    const record = vi.fn(async () => {})
    expect(await prepareResultCardOpen(row(), record, () => false)).toBe(false)
    expect(record).not.toHaveBeenCalled()
  })
  it('does not reopen an older card when a later click wins', async () => {
    let current = true, complete!: () => void
    const record = vi.fn(() => new Promise<void>(resolve => { complete = resolve }))
    const opening = prepareResultCardOpen(row(), record, () => current)
    current = false; complete()
    expect(await opening).toBe(false)
  })
  it('keeps the card closed on a failed exposure write so a title click can retry', async () => {
    const record = vi.fn().mockRejectedValueOnce(new Error('unavailable')).mockResolvedValueOnce(undefined)
    await expect(prepareResultCardOpen(row(), record, () => true)).rejects.toThrow('unavailable')
    expect(await prepareResultCardOpen(row(), record, () => true)).toBe(true)
  })
  it('binds expanded content to identity, question, answer, decision and processing state', () => {
    const value = row(), binding = resultCardBinding(value, 'student-session-one')
    expect(resultCardBinding(value, 'student-session-two')).not.toBe(binding)
    const changes: ((value: CaseDetail) => void)[] = [
      v => { v.case.id = 'case_two' },
      v => { v.case.question_id = 'question_two' },
      v => { v.case.current_question_revision++ },
      v => { v.attempt.id = 'attempt_two' },
      v => { v.attempt.recognition_revision++ },
      v => { v.case.current_decision!.id = 'decision_two' },
      v => { v.case.current_decision!.revision++ },
      v => { v.case.current_decision!.validity = 'needs_review' },
      v => { v.checkJob = { status: 'running' } as CaseDetail['checkJob'] },
      v => { v.case.progress = 'rechecking' },
    ]
    for (const change of changes) { const updated = structuredClone(value); change(updated); expect(resultCardBinding(updated, 'student-session-one')).not.toBe(binding) }
  })
  it('does not collapse from the exposure audit revision alone', () => {
    const value = row(), binding = resultCardBinding(value, 'student-session-one')
    value.attempt.revision++; value.attempt.help_condition = 'answer_shown'; value.case.revision++
    expect(resultCardBinding(value, 'student-session-one')).toBe(binding)
  })
})
