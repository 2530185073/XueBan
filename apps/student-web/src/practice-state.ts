import type { PracticeAnswer, PracticeAssessment, PracticeSession, PracticeSubmission } from './core/learning'

export const assessmentPending = (assessment: PracticeAssessment | undefined) => !!assessment && (assessment.status === 'queued' || assessment.status === 'grading')

/** A poll can only update the precise attempt still displayed by this student/session/item. */
export function applyPracticeAssessment(session: PracticeSession, assessment: PracticeAssessment): PracticeSession {
  if (session.id !== assessment.session_id || session.student_id !== assessment.student_id) return session
  const item = session.items.find(item => item.id === assessment.item_id)
  if (!item || item.response || item.assessment?.assessment_id !== assessment.assessment_id) return session
  if (!assessmentPending(item.assessment) && assessmentPending(assessment)) return session
  return { ...session, items: session.items.map(item => item.id === assessment.item_id ? { ...item, assessment } : item) }
}

/** Pending is a durable receipt, never a correctness result. Only a confirmed response fills response. */
export function applyPracticeAnswer(session: PracticeSession, sessionId: string, itemId: string, result: PracticeAnswer, submission: PracticeSubmission, hintUsed: boolean): PracticeSession {
  if (session.id !== sessionId || result.item_id !== itemId || !session.items.some(item => item.id === itemId && !item.response)) return session
  if (result.result === 'pending') {
    const assessment: PracticeAssessment = { assessment_id: result.assessment_id, student_id: session.student_id, session_id: sessionId, item_id: itemId,
      status: 'queued', result: null, feedback: result.feedback, solution_text: null, retryable: false, submission }
    return { ...session, items: session.items.map(item => item.id === itemId ? { ...item, assessment } : item) }
  }
  if (result.retryable) return session
  return { ...session, status: result.session_status, items: session.items.map(item => item.id === itemId ? { ...item,
    response: { answer_text: submission.answer_text.trim(), result: result.result, hint_used: hintUsed, feedback: result.feedback },
    solution_text: result.solution_text ?? undefined } : item) }
}
