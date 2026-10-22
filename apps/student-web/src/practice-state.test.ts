import { describe, expect, it } from 'vitest'
import type { PendingPracticeAnswer, PracticeAssessment, PracticeSession } from './core/learning'
import { session as sessionFixture } from './core/learning.fixtures'
import { applyPracticeAnswer, applyPracticeAssessment, assessmentPending } from './practice-state'

const original = (): PracticeSession => ({ ...sessionFixture, items: [sessionFixture.items[0]] } as PracticeSession)
const submission = { answer_text: '2(x+1)\n=2x+2' }
const pending: PendingPracticeAnswer = { item_id: 'item_1', result: 'pending', assessment_id: 'assessment_one', session_status: 'active', feedback: '正在核验本次作答。', solution_text: null }
const accepted = () => applyPracticeAnswer(original(), 'practice_1', 'item_1', pending, submission, false)

describe('durable asynchronous practice state', () => {
  it('stores a pending receipt and input without turning it into an answer or a completed session', () => {
    const current = accepted()
    expect(current.status).toBe('active'); expect(current.items[0].response).toBeUndefined()
    expect(current.items[0].assessment?.submission).toEqual(submission)
    expect(assessmentPending(current.items[0].assessment)).toBe(true)
    expect(current.items[0].solution_text).toBeUndefined()
  })
  it('applies terminal polling to its own attempt but relies on session read for the durable response', () => {
    const current = accepted()
    const assessment: PracticeAssessment = { ...current.items[0].assessment!, status: 'completed', result: 'correct', solution_text: '2x+2', feedback: '过程正确。' }
    const next = applyPracticeAssessment(current, assessment)
    expect(next.items[0].assessment?.result).toBe('correct')
    expect(next.items[0].response).toBeUndefined()
    expect(next.status).toBe('active')
    expect(applyPracticeAssessment(next, current.items[0].assessment!)).toBe(next)
  })
  it('does not let an older poll overwrite a newer retry, another item, session or student', () => {
    const current = accepted(), assessment = current.items[0].assessment!
    const retry = applyPracticeAnswer(current, 'practice_1', 'item_1', { ...pending, assessment_id: 'assessment_two' }, submission, false)
    expect(applyPracticeAssessment(retry, { ...assessment, status: 'completed', result: 'incorrect' })).toBe(retry)
    for (const change of [{ student_id: 'other_student' }, { session_id: 'other_session' }, { item_id: 'other_item' }]) {
      expect(applyPracticeAssessment(current, { ...assessment, ...change })).toBe(current)
    }
    expect(applyPracticeAnswer(current, 'other_session', 'item_1', pending, submission, false)).toBe(current)
  })
  it('keeps failed/superseded attempts unscored and preserves the submitted work for explicit retry', () => {
    const current = accepted()
    for (const status of ['failed', 'superseded'] as const) {
      const next = applyPracticeAssessment(current, { ...current.items[0].assessment!, status, result: 'unscorable', retryable: status === 'failed', feedback: '未完成。' })
      expect(next.items[0].response).toBeUndefined()
      expect(next.items[0].assessment?.submission?.answer_text).toBe(submission.answer_text)
      expect(assessmentPending(next.items[0].assessment)).toBe(false)
    }
    expect(applyPracticeAnswer(current, 'practice_1', 'item_1', { item_id: 'item_1', result: 'unscorable', session_status: 'active', retryable: true, solution_text: null }, submission, false)).toBe(current)
  })
  it('keeps rule-based completion working and never lets a later pending receipt erase a recorded answer', () => {
    const current = original()
    const done = applyPracticeAnswer(current, 'practice_1', 'item_1', { item_id: 'item_1', result: 'correct', session_status: 'completed', solution_text: '2x+2' }, submission, false)
    expect(done.status).toBe('completed'); expect(done.items[0].response?.result).toBe('correct')
    expect(applyPracticeAnswer(done, 'practice_1', 'item_1', pending, submission, false)).toBe(done)
  })
})
