import type { AssetAccess, AttemptRead, CorrectionCase, DecisionRead, HelpRequest, Hint, QuestionRead } from './api/contracts.generated'
import { ApiError, StaffApi } from './api/client'

export interface CaseWorkspace {
  help: HelpRequest
  correction: CorrectionCase
  question: QuestionRead
  attempts: AttemptRead[]
  hints: Hint[]
  decision: DecisionRead | null
  assets: AssetAccess[]
}

export async function loadWorkspace(api: StaffApi, help: HelpRequest, signal: AbortSignal): Promise<CaseWorkspace> {
  const correction = await api.case(help.case_id, signal)
  const [question, attempts, hints, decision] = await Promise.all([
    api.question(correction.question_id, signal),
    Promise.all(correction.attempt_ids.map(id => api.attempt(id, signal))),
    Promise.all(correction.hint_ids.map(id => api.hint(id, signal))),
    correction.current_decision ? api.decision(correction.current_decision.id, signal) : Promise.resolve(null),
  ])
  if (question.current_revision !== correction.current_question_revision || attempts.some(a => a.record.case_id !== correction.id)) {
    throw new ApiError(409, 'Workspace references changed; reload required')
  }
  const ids = [...new Set([...question.record.regions.map(r => r.asset_id), ...attempts.flatMap(a => a.record.asset_ids)])]
  const assets = await Promise.all(ids.map(id => api.asset(id, signal)))
  return { help, correction, question, attempts, hints, decision, assets }
}
