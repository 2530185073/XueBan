import type { CaseWorkspace } from './workspace'
import type { HelpRequest, IdentityContext } from './api/contracts.generated'

const time = '2026-10-16T08:32:00Z'
const region = { asset_id: 'asset_synthetic', x: 62, y: 200, width: 496, height: 190, coordinate_space: 'original_pixels' as const }
export const sampleHelp: HelpRequest = {
  id: 'help_synthetic', revision: 1, case_id: 'case_synthetic', attempt_id: 'attempt_synthetic',
  kind: 'question', note: '括号外的 3 应该乘到哪里？这一步我有点不确定。', status: 'waiting',
  claimed_by: null, created_at: time, claimed_at: null, completed_at: null, resolution_note: null,
}
export const sampleIdentity: IdentityContext = {
  actor_id: 'staff_preview', roles: ['sitting_teacher'],
  authorized_students: [{ student_id: 'student_synthetic', display_name: '合成学生 A', campus_id: 'campus_preview' }],
}
export const sampleWorkspace: CaseWorkspace = {
  help: sampleHelp,
  correction: {
    id: 'case_synthetic', revision: 3, scope: { tenant_id: 'tenant_preview', campus_id: 'campus_preview', student_id: 'student_synthetic' },
    homework_id: 'homework_synthetic', question_id: 'question_synthetic', current_question_revision: 1,
    root_attempt_id: 'attempt_synthetic', current_attempt_id: 'attempt_synthetic', progress: 'pending', current_decision: null,
    attempt_ids: ['attempt_synthetic'], check_ids: ['check_synthetic'], hint_ids: ['hint_synthetic'], help_request_ids: ['help_synthetic'], review_ids: [],
    created_at: time, updated_at: time, lifecycle: 'active', replacement_case_ids: [],
  },
  question: {
    record: { question_id: 'question_synthetic', revision: 1, homework_id: 'homework_synthetic', stem_text: '去括号：3(x − 2)。', regions: [region], source: 'recognizer', source_actor_id: null, bank_question_id: null, knowledge_point_ids: null, created_at: time, lifecycle: 'active', replacement_question_ids: [] },
    current_revision: 1, currently_effective: true,
  },
  attempts: [{
    record: { id: 'attempt_synthetic', revision: 1, case_id: 'case_synthetic', question_id: 'question_synthetic', captured_question_revision: 1, parent_attempt_id: null, root_attempt_id: 'attempt_synthetic', asset_ids: ['asset_synthetic'], recognition_revision: 1, answer_text: '3(x − 2) = 3x − 2', answer_regions: [region], recognition_status: 'ready', help_condition: 'hint_used', occurred_at: time, created_at: time },
    current_revision: 1, currently_effective: true,
  }],
  hints: [{ id: 'hint_synthetic', revision: 1, case_id: 'case_synthetic', attempt_id: 'attempt_synthetic', source_decision_id: 'decision_synthetic', source_decision_revision: 1, level: 'direction', content: '观察括号里的两项，括号外的数分别需要和谁相乘？', exposes_answer: false, source_is_provisional: true, validity: 'active', first_shown_at: '2026-10-16T08:33:00Z', created_at: time }],
  decision: {
    record: {
      id: 'decision_synthetic', revision: 1, case_id: 'case_synthetic', check_id: 'check_synthetic',
      input_versions: { question_id: 'question_synthetic', question_revision: 1, attempt_id: 'attempt_synthetic', attempt_recognition_revision: 1, case_revision_at_submission: 1, fingerprint: '0'.repeat(64) },
      origin: 'model', validity: 'candidate', verdict: 'incorrect', reason_code: null,
      basis: [{ kind: 'model_hypothesis', summary: '候选观察：去括号时，第二项可能没有与括号外的系数相乘。需要老师对照原图核实。', source_ref: null, regions: [region] }],
      first_error_region: region, confirmation_source: null, confirmed_by: null, confirmed_at: null, replaces_decision_id: null, invalidated_by_event_id: null, created_at: time,
    }, current_revision: 1, currently_effective: true,
  },
  assets: [{ asset_id: 'asset_synthetic', read_url: 'https://synthetic.invalid/sample-homework.svg', expires_at: '2099-01-01T00:00:00Z' }],
}
sampleWorkspace.correction.current_decision = sampleWorkspace.decision!.record
