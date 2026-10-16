import { describe, expect, it } from 'vitest'
import type { CaseDetail, DraftView } from './core/types'
import { casePresentation, correctionTarget, dateLabel, preferredCase, replacementTarget, stageLabels } from './presentation'

const sample = () => ({ case: { question_id: 'question_test', current_attempt_id: 'attempt_test', current_question_revision: 2, current_decision: { origin: 'model', validity: 'candidate', verdict: 'correct', input_versions: { question_id: 'question_test', attempt_id: 'attempt_test', question_revision: 2, attempt_recognition_revision: 1 } } }, attempt: { recognition_status: 'ready', recognition_revision: 1 } }) as CaseDetail

describe('student-facing evidence states', () => {
  it('labels AI candidate as preliminary and staff confirmation separately', () => { const row = sample(); expect(casePresentation(row).label).toBe('初判正确'); row.case.current_decision!.origin = 'staff'; row.case.current_decision!.validity = 'confirmed'; expect(casePresentation(row).label).toBe('老师确认正确') })
  it('never treats needs_review or unreadable answers as correct', () => { const row = sample(); row.case.current_decision!.validity = 'needs_review'; expect(casePresentation(row).tone).toBe('amber'); row.attempt.recognition_status = 'needs_revision'; expect(casePresentation(row).label).toBe('识别待核实'); expect(casePresentation(row).note).toContain('还没有检查') })
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
