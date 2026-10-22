import examples from '../../../../contracts/phase1/examples.json'
import type { Attempt, CapturedQuestionRevision, CaseRecord, CorrectionCase, Decision } from './types'
import type { KnowledgeDetail, KnowledgeEvidence } from './learning'
import { evidenceTarget, type EvidenceFocus } from './evidenceTrace'
const example = <T>(name: string) => structuredClone(examples.entries.find(x => x.name === name)!.value) as T
/** Synthetic protocol fixture, not a model or mathematics quality test. */
export function traceFixture() {
  const c = example<CorrectionCase>('resolved_case'), original = example<Attempt>('original_attempt'), correction = example<Attempt>('correction_across_question_revision')
  original.answer_text = '(-18) / 9 = 2'; correction.answer_text = '(-18) / 9 = -(18 / 9) = -2'; correction.created_at = '2026-10-16T08:20:00Z'; correction.recognition_revision = 2
  const wrong = example<Decision>('stale_old_decision'), correct = example<Decision>('model_candidate')
  Object.assign(wrong, { validity: 'candidate', revision: 1 }); wrong.input_versions.question_revision = 2; correct.input_versions.attempt_recognition_revision = 2
  c.current_decision = correct; c.progress = 'pending'; c.hint_ids = []; c.help_request_ids = []; c.review_ids = []
  const record: CaseRecord = { case: c, attempts: [{ record: original, current_revision: 1, currently_effective: false }, { record: correction, current_revision: 1, currently_effective: true }], decisions: [wrong, correct].map(d => ({ record: d, current_revision: d.revision, currently_effective: d.id === correct.id })), checks: [], hints: [], help: [], reviews: [] }
  const evidence: KnowledgeEvidence[] = [original, correction].map((a, i) => ({ id: `evidence_${i}`, occurred_at: a.occurred_at, question_text: '当前题面不得替代旧题面', homework_title: '合成算式记录', result: i ? 'correct' : 'incorrect', assistance_level: i ? 'direction' : 'unknown', event_type: i ? 'correction' : 'school_first_attempt', source_role: 'model', case_id: c.id, question_id: c.question_id, homework_id: c.homework_id, attempt_id: a.id, decision_id: i ? correct.id : wrong.id }))
  const detail: KnowledgeDetail = { node: { id: 'kp_rational', name: '有理数除法', observable_objective: null, definition: null }, graph_version: 'graph_synthetic', projection: { pending: false } as KnowledgeDetail['projection'], state: null, evidence, prerequisites: [], notice: '合成记录' }
  const questions = [example<CapturedQuestionRevision>('question_revision_1'), example<CapturedQuestionRevision>('question_revision_2')]
  questions[0].stem_text = '计算：(-18) / 9。'; questions[0].options = [{ label: 'B', text: ' -2 ' }, { label: 'A', text: '2' }]; questions[1].stem_text = '新版题面，仅供新作答'
  const focus = (index = 0): EvidenceFocus => ({ ...evidenceTarget(detail, evidence[index]), scope: 'scope_synthetic' })
  return { record, detail, questions, focus, studentId: c.scope.student_id }
}
