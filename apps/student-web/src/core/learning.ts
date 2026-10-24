/**
 * Later-phase learning DTOs (knowledge map, agent memory, voluntary practice).
 * They are not in the generated phase-one contracts, so every field the UI
 * reads is checked here by hand. Unknown enum values reject the response.
 */
export const learningStatuses = ['needs_help_now', 'consolidating', 'stable_for_now', 'no_judgment'] as const
export type LearningStatus = (typeof learningStatuses)[number]
export const evidenceResults = ['correct', 'incorrect', 'partial', 'unscorable'] as const
export type EvidenceResult = (typeof evidenceResults)[number]
export const memoryCategories = ['recurring_difficulty', 'progress_milestone', 'effective_support', 'support_agreement', 'learning_goal', 'interface_preference'] as const
export type MemoryCategory = (typeof memoryCategories)[number]
export type StudentMemoryCategory = Extract<MemoryCategory, 'interface_preference' | 'learning_goal'>
export const practiceStatuses = ['active', 'completed', 'time_up', 'exited'] as const
export type PracticeStatus = (typeof practiceStatuses)[number]
export const practiceResults = ['correct', 'incorrect', 'unscorable'] as const
export type PracticeResult = (typeof practiceResults)[number]
export const practiceAssessmentStatuses = ['queued', 'grading', 'completed', 'failed', 'superseded'] as const
export type PracticeAssessmentStatus = (typeof practiceAssessmentStatuses)[number]
export const exitReasons = ['tired', 'not_now', 'done', 'other'] as const
export type ExitReason = (typeof exitReasons)[number]
export const MEMORY_CONTENT_MAX = 120
export const MEMORY_CONTENT_MIN = 2
export const PRACTICE_ANSWER_MAX = 4000
export const MAX_PRACTICE_ITEMS = 3
export const variantGenerationStatuses = ['queued', 'generating', 'verifying', 'ready', 'failed', 'superseded'] as const
export type VariantGenerationStatus = (typeof variantGenerationStatuses)[number]
export const practiceQuestionTypes = ['single_choice', 'multiple_choice', 'fill_blank', 'calculation', 'application', 'proof', 'unknown'] as const
export type PracticeQuestionType = (typeof practiceQuestionTypes)[number]

export interface RelatedKnowledge { id: string; name: string; learning_status: LearningStatus }
export interface EvidenceSources { model_evidence_count?: number; staff_verified_evidence_count?: number; assessment_source?: 'model' | 'staff' | 'mixed' | 'none' }
export interface KnowledgeState extends EvidenceSources {
  knowledge_id: string; name: string; chapter_id: string | null; chapter_name: string | null
  learning_status: LearningStatus; reason_codes: string[]; sufficiency_explanation: string | null; sort_order: number
}
export interface KnowledgeGraphNode extends EvidenceSources {
  id: string; node_type: string; parent_id: string | null; name: string
  definition: string | null; observable_objective: string | null; sort_order: number
  subject: string | null; chapter_id: string | null; chapter_name: string | null
  learning_status: LearningStatus; reason_codes: string[]; valid_evidence_count: number
}
export interface KnowledgeGraphEdge {
  id: string; from_id: string; to_id: string; relation_type: 'prerequisite_of'; scope: string | null; rationale: string | null
}
export interface LearningProjection { pending: boolean; projected_at: string | null; evidence_revision?: number; state_version?: number }
export interface ChapterRollup { chapter_id: string; name: string; objectives: number; status_counts: Partial<Record<LearningStatus, number>> }
export interface CurriculumScope {
  grade: number; subject: string; publisher_code: string; publisher_name: string; stage: 'primary' | 'junior_middle'
  revision_policy: 'publisher_latest' | 'explicit_book'; textbook_revision: 'publisher_latest' | `isbn:${string}`
  catalog_status: 'available' | 'incomplete' | 'unavailable' | 'publisher_mismatch'; catalog_verified_at: string | null
  catalog_candidates: string[]
  books: CurriculumBook[]
}
export interface CurriculumBook {
  term: 'up' | 'down' | 'full'; title: string; isbn: string | null; catalog_year: number | null; updated_at: string | null
  revised_2022_standard: boolean; contents_review_status: string
}
export interface CurriculumSubject extends CurriculumScope {
  graph_ready: boolean; status: 'building' | 'ready'; correct_count: number; incorrect_count: number
  partial_count: number; scorable_count: number; score_percent: number | null
}
export interface CurriculumCatalog { revision_policy: 'publisher_latest'; grades: number[]; subjects: Record<string, CurriculumScope[]>; notice: string }
export interface StudentCurriculum { student_id: string; grade: number | null; stage: 'primary' | 'junior_middle' | null; subjects: CurriculumScope[]; notice: string }
export interface CurriculumBookIdentity {
  graph_version: string; title: string; subject: string; grade: number; publisher_code: string; publisher_name: string
  isbn: string | null; term: 'up' | 'down' | 'full'; review_status: string; review_label: string; source_note: string | null
}
export interface CurriculumBooks { student_id: string; profile_revision: number; current_binding: CurriculumBookIdentity | null; books: CurriculumBookIdentity[] }
export interface MappingCoverageItem {
  case_id: string; question_id: string; homework_id: string; label: string; subject: string; stem_preview: string
  source_role: 'model' | 'staff'; occurred_at: string; state?: 'no_direct_match' | 'mapping_pending' | 'not_linked'
}
export interface MappingCoverage {
  scope: 'current_reliable_errors_in_selected_graph'; time_basis: 'current_attempt_occurred_at'; graph_version: string | null
  window: { start: string | null; end_exclusive: string | null }; available: boolean
  total: number; linked: number; unlinked: number; model_count: number; staff_count: number
  items: MappingCoverageItem[]; truncated: boolean; notice: string
}
export interface KnowledgeMap {
  mapping_coverage?: MappingCoverage
  student_id: string; grade?: number | null; projection: LearningProjection
  graph_version?: string | null; graph_review_status?: string
  nodes?: KnowledgeGraphNode[]; edges?: KnowledgeGraphEdge[]; book_identity?: CurriculumBookIdentity | null; source_note?: string | null
  states: KnowledgeState[]; chapters: ChapterRollup[]; subjects?: CurriculumSubject[]; notice: string
}
export interface KnowledgeEvidence { id: string; occurred_at: string; question_text: string | null; homework_title: string | null; result: EvidenceResult; assistance_level: string; event_type: string; source_role?: 'model' | 'sitting_teacher' | 'unknown'; case_id?: string; question_id?: string; homework_id?: string; attempt_id?: string; parent_attempt_id?: string | null; decision_id?: string; answer_text?: string | null; reference_answer?: string | null; item_family_id?: string; independent?: boolean }
export interface KnowledgeDetail {
  node: { id: string; name: string; observable_objective: string | null; definition: string | null }
  state: ({ learning_status: LearningStatus; reason_codes: string[] } & EvidenceSources) | null
  evidence: KnowledgeEvidence[]; prerequisites: RelatedKnowledge[]; leads_to?: RelatedKnowledge[]; notice: string
  projection?: LearningProjection; graph_version?: string | null
}
export interface AgentTip { kind: 'past_difficulty' | 'past_progress' | 'agreed_support'; text: string; memory_id: string; evidence_ids: string[] }
export interface AgentContext { case_id: string; tips: AgentTip[]; knowledge: RelatedKnowledge[]; notice: string }
export interface Memory { id: string; category: MemoryCategory; content: string; source: string; status: string; confirmed_by: string | null; valid_until: string | null; created_at: string | null }
export interface MemoryList { student_id: string; items: Memory[]; notice: string }
export interface VariantOffer { knowledge_id: string; name: string; learning_status: LearningStatus; available_items: number }
export interface PreviewOffer { knowledge_ids: string[]; names: string[]; quiz_items_available: number }
export interface OptionalActivities { gate: { open: boolean; reasons: string[]; remaining_minutes: number }; variants: VariantOffer[]; preview: PreviewOffer | null; notice: string }
export interface VariantErrorQuestion {
  case_id: string; question_id: string; question_text: string | null; homework_title: string | null
  occurred_at: string; correct_answer: string | null; correction_method: string | null
  curriculum_status?: 'current' | 'historical_unmatched'
}
export interface VariantErrorGroup {
  knowledge_id: string; name: string; learning_status: LearningStatus; error_count: number
  source_case_id: string; available_items: number; questions: VariantErrorQuestion[]
  graph_version?: string | null; curriculum_status?: 'current' | 'historical_unmatched'
}
export interface VariantPracticeOverview {
  student_id: string; projection: { pending: boolean; projected_at: string | null }
  gate: { open: boolean; reasons: string[]; remaining_minutes: number }
  groups: VariantErrorGroup[]; pending_count: number; notice: string
}
export interface VariantGeneration {
  job_id: string; student_id: string; status: VariantGenerationStatus; source_case_id: string
  graph_version: string | null; knowledge_id: string | null; question_type: PracticeQuestionType
  item_id: string | null; failure_code: string | null; notice: string; session_id?: string | null
}
export interface VariantGenerationList { student_id: string; items: VariantGeneration[] }
export interface GenerateVariant { source_case_id: string; willing: true; count: 1; knowledge_id?: string }
export interface PracticeSubmission {
  answer_text: string
  selected_options?: string[]
  answers?: { field_id: string; answer_text: string }[]
}
export interface PracticeAssessment {
  assessment_id: string; student_id: string; session_id: string; item_id: string; status: PracticeAssessmentStatus
  result: PracticeResult | null; feedback: string; solution_text: string | null; retryable: boolean
  submission?: { answer_text: string | null; selected_options?: string[] | null; answers?: { field_id: string; answer_text: string }[] | null }
}
export interface PracticeItem {
  id: string; knowledge_id: string | null; stem_text: string; answer_kind: 'number' | 'choice' | 'text'; has_hint: boolean
  graph_version?: string | null; question_type?: PracticeQuestionType; question_kind?: 'choice' | 'fill_blank' | 'solution'
  options?: { label: string; text: string }[]; answer_fields?: { id: string; label: string }[]
  verification?: { source: 'ai' | 'staff'; status: 'verified'; generator_model?: string; verifier_model?: string; heterogeneous_models?: boolean }
  response?: { answer_text: string; result: PracticeResult; hint_used: boolean; feedback?: string }; solution_text?: string
  assessment?: PracticeAssessment
}
export interface PreviewTarget { id: string; name: string; objective: string | null; prerequisites: RelatedKnowledge[] }
export interface PracticeSession {
  id: string; student_id: string; purpose: 'variant' | 'preview'; knowledge_ids: string[]; status: PracticeStatus; exit_reason: ExitReason | null
  graph_version?: string | null
  items: PracticeItem[]; started_at: string; budget_ends_at: string; ended_at: string | null; hints_allowed: boolean; notice: string; targets?: PreviewTarget[]
}
export interface PracticeSessionList { items: PracticeSession[] }
export interface CompletedPracticeAnswer { item_id: string; result: PracticeResult; solution_text: string | null; session_status: PracticeStatus; notice?: string; retryable?: boolean; feedback?: string }
export interface PendingPracticeAnswer { item_id: string; result: 'pending'; assessment_id: string; solution_text: null; session_status: 'active'; feedback: string }
export type PracticeAnswer = CompletedPracticeAnswer | PendingPracticeAnswer
export interface PracticeHint { item_id: string; hint_text: string }
export type StartPractice =
  | { purpose: 'variant'; willing: true; knowledge_id: string; count: number; source_case_id?: string }
  | { purpose: 'variant'; willing: true; generation_job_id: string; count: 1 }
  | { purpose: 'preview'; willing: true; with_quiz: boolean }

type Guard<T> = (value: unknown) => value is T
type Fields = Record<string, unknown>
const record = (value: unknown): value is Fields => typeof value === 'object' && value !== null && !Array.isArray(value)
const text = (value: unknown, max = 4000): value is string => typeof value === 'string' && value.length <= max
const id = (value: unknown): value is string => typeof value === 'string' && value.length > 0 && value.length <= 200
const nullable = <T>(guard: Guard<T>) => (value: unknown): value is T | null => value === null || guard(value)
const optional = <T>(guard: Guard<T>) => (value: unknown): value is T | undefined => value === undefined || guard(value)
const oneOf = <T extends string>(values: readonly T[]) => (value: unknown): value is T => typeof value === 'string' && (values as readonly string[]).includes(value)
const count = (value: unknown): value is number => Number.isInteger(value) && (value as number) >= 0 && (value as number) <= 100_000
const gradeValue = (value: unknown): value is number => Number.isInteger(value) && (value as number) >= 5 && (value as number) <= 8
const textbookRevision = (value: unknown): value is CurriculumScope['textbook_revision'] => value === 'publisher_latest' || (typeof value === 'string' && /^isbn:[0-9]{13}$/.test(value))
const percentage = (value: unknown): value is number => Number.isInteger(value) && (value as number) >= 0 && (value as number) <= 100
const date = (value: unknown): value is string => typeof value === 'string' && value.length <= 64 && Number.isFinite(Date.parse(value))
const list = <T>(guard: Guard<T>, max = 500) => (value: unknown): value is T[] => Array.isArray(value) && value.length <= max && value.every(item => guard(item))
const shape = <T>(fields: Record<string, (value: unknown) => boolean>): Guard<T> => (value: unknown): value is T => record(value) && Object.entries(fields).every(([key, guard]) => guard(value[key]))

const status = oneOf(learningStatuses)
const evidenceSourceFields = { model_evidence_count: optional(count), staff_verified_evidence_count: optional(count), assessment_source: optional(oneOf(['model', 'staff', 'mixed', 'none'] as const)) }
const isRelated = shape<RelatedKnowledge>({ id, name: text, learning_status: status })
export const isKnowledgeState = shape<KnowledgeState>({ ...evidenceSourceFields, knowledge_id: id, name: text, chapter_id: nullable(id), chapter_name: nullable(text), learning_status: status, reason_codes: list(text, 20), sufficiency_explanation: optional(nullable(text)), sort_order: Number.isFinite })
const isStatusCounts = (value: unknown): value is ChapterRollup['status_counts'] => record(value) && Object.entries(value).every(([key, n]) => status(key) && count(n))
const isChapter = shape<ChapterRollup>({ chapter_id: id, name: text, objectives: count, status_counts: isStatusCounts })
const isCurriculumBook = shape<CurriculumBook>({ term: oneOf(['up', 'down', 'full'] as const), title: text, isbn: nullable(text), catalog_year: nullable(count), updated_at: nullable(date), revised_2022_standard: v => typeof v === 'boolean', contents_review_status: text })
const isCurriculumScope = shape<CurriculumScope>({ grade: v => Number.isInteger(v) && (v as number) >= 5 && (v as number) <= 8, subject: text, publisher_code: text, publisher_name: text, stage: oneOf(['primary', 'junior_middle'] as const), revision_policy: oneOf(['publisher_latest', 'explicit_book'] as const), textbook_revision: textbookRevision, catalog_status: oneOf(['available', 'incomplete', 'unavailable', 'publisher_mismatch'] as const), catalog_verified_at: nullable(date), catalog_candidates: list(text, 30), books: list(isCurriculumBook, 4) })
const isCurriculumSubject = shape<CurriculumSubject>({ grade: gradeValue, subject: text, publisher_code: text, publisher_name: text, stage: oneOf(['primary', 'junior_middle'] as const), revision_policy: oneOf(['publisher_latest', 'explicit_book'] as const), textbook_revision: textbookRevision, catalog_status: oneOf(['available', 'incomplete', 'unavailable', 'publisher_mismatch'] as const), catalog_verified_at: nullable(date), catalog_candidates: list(text, 30), books: list(isCurriculumBook, 4), graph_ready: v => typeof v === 'boolean', status: oneOf(['building', 'ready'] as const), correct_count: count, incorrect_count: count, partial_count: count, scorable_count: count, score_percent: nullable(percentage) })
const isCurriculumCatalog = (value: unknown): value is CurriculumCatalog => record(value) && value.revision_policy === 'publisher_latest' && Array.isArray(value.grades) && value.grades.every(g => count(g)) && record(value.subjects) && Object.values(value.subjects).every(items => Array.isArray(items) && items.every(isCurriculumScope)) && text(value.notice)
const isStudentCurriculum = shape<StudentCurriculum>({ student_id: id, grade: nullable(gradeValue), stage: nullable(oneOf(['primary', 'junior_middle'] as const)), subjects: list(isCurriculumScope), notice: text })
const isProjection = shape<LearningProjection>({ pending: v => typeof v === 'boolean', projected_at: nullable(date), evidence_revision: optional(count), state_version: optional(count) })
const isGraphNode = shape<KnowledgeGraphNode>({ ...evidenceSourceFields, id, node_type: text, parent_id: nullable(id), name: text, definition: nullable(text), observable_objective: nullable(text), sort_order: Number.isFinite, subject: nullable(text), chapter_id: nullable(id), chapter_name: nullable(text), learning_status: status, reason_codes: list(text, 20), valid_evidence_count: count })
const isGraphEdge = shape<KnowledgeGraphEdge>({ id, from_id: id, to_id: id, relation_type: oneOf(['prerequisite_of'] as const), scope: nullable(text), rationale: nullable(text) })
const isBookIdentity = shape<CurriculumBookIdentity>({ graph_version: id, title: text, subject: text, grade: gradeValue, publisher_code: text, publisher_name: text, isbn: nullable(text), term: oneOf(['up', 'down', 'full'] as const), review_status: text, review_label: text, source_note: nullable(text) })
export const isCurriculumBooks = shape<CurriculumBooks>({ student_id: id, profile_revision: count, current_binding: nullable(isBookIdentity), books: list(isBookIdentity, 100) })
const mappingCoverageShape = shape<MappingCoverage>({
  scope: oneOf(['current_reliable_errors_in_selected_graph'] as const), time_basis: oneOf(['current_attempt_occurred_at'] as const),
  graph_version: nullable(id), window: shape({ start: nullable(date), end_exclusive: nullable(date) }), available: v => typeof v === 'boolean',
  total: count, linked: count, unlinked: count, model_count: count, staff_count: count, truncated: v => typeof v === 'boolean', notice: text,
  items: list(shape<MappingCoverageItem>({ case_id: id, question_id: id, homework_id: id, label: text, subject: text, stem_preview: text,
    source_role: oneOf(['model', 'staff'] as const), occurred_at: date, state: optional(oneOf(['no_direct_match', 'mapping_pending', 'not_linked'] as const)) }), 30),
})
export const isMappingCoverage: Guard<MappingCoverage> = (value): value is MappingCoverage => mappingCoverageShape(value)
  && value.total === value.linked + value.unlinked && value.total === value.model_count + value.staff_count
  && value.items.length <= value.unlinked && value.truncated === (value.unlinked > value.items.length)
export const isKnowledgeMap = shape<KnowledgeMap>({ mapping_coverage: optional(isMappingCoverage), student_id: id, grade: optional(nullable(gradeValue)), graph_version: optional(nullable(id)), graph_review_status: optional(text), projection: isProjection, nodes: optional(list(isGraphNode, 3000)), edges: optional(list(isGraphEdge, 6000)), book_identity: optional(nullable(isBookIdentity)), source_note: optional(nullable(text)), states: list(isKnowledgeState), chapters: list(isChapter), subjects: optional(list(isCurriculumSubject)), notice: text })
export { isCurriculumCatalog, isStudentCurriculum }
const isEvidence = shape<KnowledgeEvidence>({ id, occurred_at: date, question_text: nullable(text), homework_title: nullable(text), result: oneOf(evidenceResults), assistance_level: text, event_type: text, source_role: optional(oneOf(['model', 'sitting_teacher', 'unknown'] as const)), case_id: optional(id), question_id: optional(id), homework_id: optional(id), attempt_id: optional(id), decision_id: optional(id) })
export const isKnowledgeDetail = shape<KnowledgeDetail>({
  node: shape({ id, name: text, observable_objective: nullable(text), definition: nullable(text) }),
  state: nullable(shape({ ...evidenceSourceFields, learning_status: status, reason_codes: list(text, 20) })),
  evidence: list(isEvidence, 1000), prerequisites: list(isRelated), leads_to: optional(list(isRelated)), notice: text,
  projection: optional(isProjection), graph_version: optional(nullable(id)),
})
const isTip = shape<AgentTip>({ kind: oneOf(['past_difficulty', 'past_progress', 'agreed_support'] as const), text, memory_id: id, evidence_ids: list(id) })
export const isAgentContext = shape<AgentContext>({ case_id: id, tips: list(isTip, 50), knowledge: list(isRelated, 50), notice: text })
export const isMemory = shape<Memory>({ id, category: oneOf(memoryCategories), content: text, source: text, status: text, confirmed_by: nullable(text), valid_until: nullable(date), created_at: nullable(date) })
export const isMemoryList = shape<MemoryList>({ student_id: id, items: list(isMemory), notice: text })
const isVariant = shape<VariantOffer>({ knowledge_id: id, name: text, learning_status: status, available_items: count })
const isPreview = shape<PreviewOffer>({ knowledge_ids: list(id, 20), names: list(text, 20), quiz_items_available: count })
export const isOptionalActivities = shape<OptionalActivities>({ gate: shape({ open: v => typeof v === 'boolean', reasons: list(text, 20), remaining_minutes: count }), variants: list(isVariant, 100), preview: nullable(isPreview), notice: text })
const isVariantCurriculumStatus = optional(oneOf(['current', 'historical_unmatched'] as const))
const isVariantQuestion = shape<VariantErrorQuestion>({ case_id: id, question_id: id, question_text: nullable(text), homework_title: nullable(text), occurred_at: date, correct_answer: nullable(text), correction_method: nullable(text), curriculum_status: isVariantCurriculumStatus })
const isVariantGroup = shape<VariantErrorGroup>({ knowledge_id: id, name: text, learning_status: status, error_count: count, source_case_id: id, available_items: count, questions: list(isVariantQuestion, 50), graph_version: optional(nullable(id)), curriculum_status: isVariantCurriculumStatus })
export const isVariantPracticeOverview = shape<VariantPracticeOverview>({
  student_id: id, projection: shape({ pending: v => typeof v === 'boolean', projected_at: nullable(date) }),
  gate: shape({ open: v => typeof v === 'boolean', reasons: list(text, 20), remaining_minutes: count }),
  groups: list(isVariantGroup, 100), pending_count: count, notice: text,
})
const generationShape = shape<VariantGeneration>({
  job_id: id, student_id: id, status: oneOf(variantGenerationStatuses), source_case_id: id,
  graph_version: nullable(id), knowledge_id: nullable(id), question_type: oneOf(practiceQuestionTypes),
  item_id: nullable(id), failure_code: nullable(id), notice: text, session_id: optional(nullable(id)),
})
export const isVariantGeneration: Guard<VariantGeneration> = (value): value is VariantGeneration =>
  generationShape(value) && (value.status !== 'ready' || value.item_id !== null)
export const isVariantGenerationList = shape<VariantGenerationList>({ student_id: id, items: list(isVariantGeneration, 20) })
const isPracticeAssessmentShape = shape<PracticeAssessment>({
  assessment_id: id, student_id: id, session_id: id, item_id: id, status: oneOf(practiceAssessmentStatuses),
  result: nullable(oneOf(practiceResults)), feedback: text, solution_text: nullable(text), retryable: v => typeof v === 'boolean',
  submission: optional(shape({ answer_text: nullable(text), selected_options: optional(nullable(list(id, 10))), answers: optional(nullable(list(shape({ field_id: id, answer_text: text }), 12))) })),
})
export const isPracticeAssessment: Guard<PracticeAssessment> = (value): value is PracticeAssessment => {
  if (!isPracticeAssessmentShape(value)) return false
  if (value.status === 'queued' || value.status === 'grading') return value.result === null && value.solution_text === null && !value.retryable
  if (value.status === 'completed') return value.result !== null && !value.retryable
  return value.result === 'unscorable' && value.solution_text === null && value.retryable === (value.status === 'failed')
}
const isItem = shape<PracticeItem>({
  id, knowledge_id: nullable(id), stem_text: text, answer_kind: oneOf(['number', 'choice', 'text'] as const), has_hint: v => typeof v === 'boolean',
  graph_version: optional(nullable(id)), question_type: optional(oneOf(practiceQuestionTypes)), question_kind: optional(oneOf(['choice', 'fill_blank', 'solution'] as const)),
  options: optional(list(shape({ label: id, text }), 10)), answer_fields: optional(list(shape({ id, label: text }), 12)),
  verification: optional(shape({ source: oneOf(['ai', 'staff'] as const), status: oneOf(['verified'] as const), generator_model: optional(text), verifier_model: optional(text), heterogeneous_models: optional(v => typeof v === 'boolean') })),
  response: optional(shape({ answer_text: text, result: oneOf(practiceResults), hint_used: v => typeof v === 'boolean', feedback: optional(text) })), solution_text: optional(text),
  assessment: optional(isPracticeAssessment),
})
const isTarget = shape<PreviewTarget>({ id, name: text, objective: nullable(text), prerequisites: list(isRelated, 50) })
const isPracticeSessionShape = shape<PracticeSession>({
  id, student_id: id, purpose: oneOf(['variant', 'preview'] as const), knowledge_ids: list(id, 20), status: oneOf(practiceStatuses), exit_reason: nullable(oneOf(exitReasons)),
  graph_version: optional(nullable(id)),
  items: list(isItem, 20), started_at: date, budget_ends_at: date, ended_at: nullable(date), hints_allowed: v => typeof v === 'boolean', notice: text, targets: optional(list(isTarget, 20)),
})
export const isPracticeSession: Guard<PracticeSession> = (value): value is PracticeSession => isPracticeSessionShape(value) &&
  value.items.every(item => !item.assessment || (item.assessment.item_id === item.id && item.assessment.session_id === value.id && item.assessment.student_id === value.student_id))
export const isPracticeSessionList = shape<PracticeSessionList>({ items: list(isPracticeSession, 100) })
const isCompletedPracticeAnswer = shape<CompletedPracticeAnswer>({ item_id: id, result: oneOf(practiceResults), solution_text: nullable(text), session_status: oneOf(practiceStatuses), notice: optional(text), retryable: optional(v => typeof v === 'boolean'), feedback: optional(text) })
const isPendingPracticeAnswer = shape<PendingPracticeAnswer>({ item_id: id, result: oneOf(['pending'] as const), solution_text: v => v === null, session_status: oneOf(['active'] as const), assessment_id: id, feedback: text })
export const isPracticeAnswer: Guard<PracticeAnswer> = (value): value is PracticeAnswer => isCompletedPracticeAnswer(value) || isPendingPracticeAnswer(value)
export const isPracticeHint = shape<PracticeHint>({ item_id: id, hint_text: text })
