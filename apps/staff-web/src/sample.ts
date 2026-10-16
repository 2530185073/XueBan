import type { CaseWorkspace } from './workspace'
import type { HelpRequest, IdentityContext, QuestionRead, Region } from './api/contracts.generated'
import type { ReviewQueueItem } from './api/types'

// Everything here is synthetic. The preview draws public/sample-homework.svg (620 × 780) and never calls the service.
const time = '2026-10-16T08:32:00Z'
const scope = { tenant_id: 'tenant_preview', campus_id: 'campus_preview', student_id: 'student_synthetic' }
const box = (x: number, y: number, width: number, height: number): Region => ({ asset_id: 'asset_synthetic', x, y, width, height, coordinate_space: 'original_pixels' })
const questionRegion = box(50, 196, 520, 190)
const answerRegion = box(92, 258, 310, 52)

export const sampleImage = 'sample-homework.svg'
export const sampleCaseId = 'case_' + '0'.repeat(32)

export const sampleHelp: HelpRequest = {
  id: 'help_synthetic', revision: 1, case_id: sampleCaseId, attempt_id: 'attempt_synthetic',
  kind: 'question', note: '括号外的 3 应该乘到哪里？这一步我有点不确定。', status: 'waiting',
  claimed_by: null, created_at: time, claimed_at: null, completed_at: null, resolution_note: null,
}
export const sampleIdentity: IdentityContext = {
  actor_id: 'staff_preview', roles: ['sitting_teacher'],
  authorized_students: [{ student_id: 'student_synthetic', display_name: '合成学生 A', campus_id: 'campus_preview' }],
}
export const sampleReviewItem: ReviewQueueItem = {
  case_id: sampleCaseId, homework_id: 'homework_synthetic', student_id: 'student_synthetic', student_name: '合成学生 A',
  homework_title: '整式的加减（合成）', reason: 'verify_candidate', progress: 'pending', updated_at: time,
}

const question = (id: string, stem: string, regions: Region[]): QuestionRead => ({
  record: { question_id: id, revision: 1, homework_id: 'homework_synthetic', stem_text: stem, regions, source: 'recognizer', source_actor_id: null, bank_question_id: null, knowledge_point_ids: null, created_at: time, lifecycle: 'active', replacement_question_ids: [] },
  current_revision: 1, currently_effective: true,
})
export const sampleQuestions: QuestionRead[] = [
  question('question_synthetic', '去括号：3(x − 2)。', [questionRegion]),
  question('question_synthetic_two', '合并同类项：2a + 3a。', [box(50, 412, 520, 128)]),
  question('question_synthetic_three', '化简：5y − 2y。', [box(50, 572, 520, 122)]),
]

export const sampleWorkspace: CaseWorkspace = {
  correction: {
    id: sampleCaseId, revision: 3, scope, homework_id: 'homework_synthetic', question_id: 'question_synthetic', current_question_revision: 1,
    root_attempt_id: 'attempt_synthetic', current_attempt_id: 'attempt_synthetic', progress: 'pending', current_decision: null,
    attempt_ids: ['attempt_synthetic'], check_ids: ['check_synthetic'], hint_ids: ['hint_synthetic'], help_request_ids: ['help_synthetic'], review_ids: [],
    created_at: time, updated_at: time, lifecycle: 'active', replacement_case_ids: [],
  },
  homework: {
    id: 'homework_synthetic', revision: 4, scope, curriculum: { stage: 'junior_middle', publisher_code: 'PEP', grade: 7, subject: 'math', volume: 'first', edition: null },
    title: '整式的加减（合成）', created_at: time, occurred_at: time, coverage: { collection_scope: 'partial', unrecognized_regions: [], note: null },
    asset_ids: ['asset_synthetic'], question_ids: sampleQuestions.map(q => q.record.question_id), case_ids: [sampleCaseId], job_ids: [], processing_blocked: false,
  },
  question: sampleQuestions[0],
  attempts: [{
    record: { id: 'attempt_synthetic', revision: 1, case_id: sampleCaseId, question_id: 'question_synthetic', captured_question_revision: 1, parent_attempt_id: null, root_attempt_id: 'attempt_synthetic', asset_ids: ['asset_synthetic'], recognition_revision: 1, answer_text: '3(x − 2) = 3x − 2', answer_regions: [answerRegion], recognition_status: 'ready', help_condition: 'hint_used', occurred_at: time, created_at: time },
    current_revision: 1, currently_effective: true,
  }],
  hints: [{ id: 'hint_synthetic', revision: 1, case_id: sampleCaseId, attempt_id: 'attempt_synthetic', source_decision_id: 'decision_synthetic', source_decision_revision: 1, level: 'direction', content: '观察括号里的两项，括号外的数分别需要和谁相乘？', exposes_answer: false, source_is_provisional: true, validity: 'active', first_shown_at: '2026-10-16T08:33:00Z', created_at: time }],
  decision: {
    record: {
      id: 'decision_synthetic', revision: 1, case_id: sampleCaseId, check_id: 'check_synthetic',
      input_versions: { question_id: 'question_synthetic', question_revision: 1, attempt_id: 'attempt_synthetic', attempt_recognition_revision: 1, case_revision_at_submission: 1, fingerprint: '0'.repeat(64) },
      origin: 'model', validity: 'candidate', verdict: 'incorrect', reason_code: null,
      basis: [{ kind: 'model_hypothesis', summary: '候选观察：去括号时，第二项可能没有与括号外的系数相乘。需要老师对照原图核实。', source_ref: null, regions: [answerRegion] }],
      first_error_region: answerRegion, confirmation_source: null, confirmed_by: null, confirmed_at: null, replaces_decision_id: null, invalidated_by_event_id: null, created_at: time,
    }, current_revision: 1, currently_effective: true,
  },
  check: { id: 'check_synthetic', revision: 1, job_id: 'job_synthetic', case_id: sampleCaseId, input_versions: { question_id: 'question_synthetic', question_revision: 1, attempt_id: 'attempt_synthetic', attempt_recognition_revision: 1, case_revision_at_submission: 1, fingerprint: '0'.repeat(64) }, model_version: 'synthetic-preview', rule_version: null, prompt_version: null, decision_ids: ['decision_synthetic'], created_at: time },
  helps: [sampleHelp],
  reviews: [],
  assets: [{ id: 'asset_synthetic', revision: 2, homework_id: 'homework_synthetic', status: 'uploaded', page_index: 0, sha256: 'a'.repeat(64), byte_size: 4096, mime_type: 'image/png', width: 620, height: 780, source_asset_id: null, source_region: null, created_at: time }],
}
sampleWorkspace.correction.current_decision = sampleWorkspace.decision!.record
