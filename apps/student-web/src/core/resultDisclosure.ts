import type { CaseDetail } from './types'
import { effectiveDecision } from './student'
import { studentResultState } from '../presentation'

/** An opened card belongs to an exact answer, not just a reused question id. */
export function resultCardBinding(row: CaseDetail, scope: string | null | undefined): string {
  const decision = effectiveDecision(row)
  return JSON.stringify([scope, row.case.id, row.case.question_id, row.case.current_question_revision,
    row.attempt.id, row.attempt.recognition_revision, row.attempt.recognition_status, decision?.id, decision?.revision,
    decision?.validity, decision?.verdict, studentResultState(row), row.case.progress])
}

export function resultCardSolutionUsable(row: CaseDetail): boolean {
  const decision = effectiveDecision(row)
  return ['correct', 'incorrect'].includes(studentResultState(row)) && !!decision
    && decision.validity !== 'needs_review' && decision.verdict !== 'unable_to_judge'
    && !['editing', 'rechecking'].includes(row.case.progress) && row.attempt.recognition_status !== 'pending'
}

export function resultCardShowsSolution(row: CaseDetail): boolean {
  const decision = effectiveDecision(row)
  return resultCardSolutionUsable(row) && !!decision
    && !!(studentResultState(row) === 'incorrect' && (decision.correct_answer?.trim() || decision.correction_method?.trim())
      || decision.answer_checks?.some(part => part.verdict === 'incorrect' && part.correct_answer))
}

/** The same click opens the card and records any solution it will show. */
export async function prepareResultCardOpen(row: CaseDetail, record: (row: CaseDetail) => Promise<void>, stillCurrent: () => boolean): Promise<boolean> {
  if (!stillCurrent()) return false
  if (resultCardShowsSolution(row)) await record(row)
  return stillCurrent()
}
