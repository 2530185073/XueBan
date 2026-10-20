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
export const exitReasons = ['tired', 'not_now', 'done', 'other'] as const
export type ExitReason = (typeof exitReasons)[number]
export const MEMORY_CONTENT_MAX = 120
export const MEMORY_CONTENT_MIN = 2
export const PRACTICE_ANSWER_MAX = 200
export const MAX_PRACTICE_ITEMS = 3

export interface RelatedKnowledge { id: string; name: string; learning_status: LearningStatus }
export interface KnowledgeState {
  knowledge_id: string; name: string; chapter_id: string | null; chapter_name: string | null
  learning_status: LearningStatus; reason_codes: string[]; sufficiency_explanation: string | null; sort_order: number
}
export interface ChapterRollup { chapter_id: string; name: string; objectives: number; status_counts: Partial<Record<LearningStatus, number>> }
export interface CurriculumScope {
  grade: number; subject: string; publisher_code: string; publisher_name: string; stage: 'primary' | 'junior_middle'
  revision_policy: 'publisher_latest'; textbook_revision: 'publisher_latest'
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
export interface KnowledgeMap {
  student_id: string; grade?: number | null; projection: { pending: boolean; projected_at: string | null }
  states: KnowledgeState[]; chapters: ChapterRollup[]; subjects?: CurriculumSubject[]; notice: string
}
export interface KnowledgeEvidence { id: string; occurred_at: string; question_text: string | null; homework_title: string | null; result: EvidenceResult; assistance_level: string; event_type: string }
export interface KnowledgeDetail {
  node: { id: string; name: string; observable_objective: string | null; definition: string | null }
  state: { learning_status: LearningStatus; reason_codes: string[] } | null
  evidence: KnowledgeEvidence[]; prerequisites: RelatedKnowledge[]; notice: string
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
}
export interface VariantErrorGroup {
  knowledge_id: string; name: string; learning_status: LearningStatus; error_count: number
  source_case_id: string; available_items: number; questions: VariantErrorQuestion[]
}
export interface VariantPracticeOverview {
  student_id: string; projection: { pending: boolean; projected_at: string | null }
  gate: { open: boolean; reasons: string[]; remaining_minutes: number }
  groups: VariantErrorGroup[]; pending_count: number; notice: string
}
export interface PracticeItem {
  id: string; knowledge_id: string; stem_text: string; answer_kind: 'number' | 'choice' | 'text'; has_hint: boolean
  response?: { answer_text: string; result: PracticeResult; hint_used: boolean }; solution_text?: string
}
export interface PreviewTarget { id: string; name: string; objective: string | null; prerequisites: RelatedKnowledge[] }
export interface PracticeSession {
  id: string; student_id: string; purpose: 'variant' | 'preview'; knowledge_ids: string[]; status: PracticeStatus; exit_reason: ExitReason | null
  items: PracticeItem[]; started_at: string; budget_ends_at: string; ended_at: string | null; hints_allowed: boolean; notice: string; targets?: PreviewTarget[]
}
export interface PracticeSessionList { items: PracticeSession[] }
export interface PracticeAnswer { item_id: string; result: PracticeResult; solution_text: string; session_status: PracticeStatus }
export interface PracticeHint { item_id: string; hint_text: string }
export type StartPractice =
  | { purpose: 'variant'; willing: true; knowledge_id: string; count: number; source_case_id?: string }
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
const percentage = (value: unknown): value is number => Number.isInteger(value) && (value as number) >= 0 && (value as number) <= 100
const date = (value: unknown): value is string => typeof value === 'string' && value.length <= 64 && Number.isFinite(Date.parse(value))
const list = <T>(guard: Guard<T>, max = 500) => (value: unknown): value is T[] => Array.isArray(value) && value.length <= max && value.every(item => guard(item))
const shape = <T>(fields: Record<string, (value: unknown) => boolean>): Guard<T> => (value: unknown): value is T => record(value) && Object.entries(fields).every(([key, guard]) => guard(value[key]))

const status = oneOf(learningStatuses)
const isRelated = shape<RelatedKnowledge>({ id, name: text, learning_status: status })
export const isKnowledgeState = shape<KnowledgeState>({ knowledge_id: id, name: text, chapter_id: nullable(id), chapter_name: nullable(text), learning_status: status, reason_codes: list(text, 20), sufficiency_explanation: optional(nullable(text)), sort_order: Number.isFinite })
const isStatusCounts = (value: unknown): value is ChapterRollup['status_counts'] => record(value) && Object.entries(value).every(([key, n]) => status(key) && count(n))
const isChapter = shape<ChapterRollup>({ chapter_id: id, name: text, objectives: count, status_counts: isStatusCounts })
const isCurriculumBook = shape<CurriculumBook>({ term: oneOf(['up', 'down', 'full'] as const), title: text, isbn: nullable(text), catalog_year: nullable(count), updated_at: nullable(date), revised_2022_standard: v => typeof v === 'boolean', contents_review_status: text })
const isCurriculumScope = shape<CurriculumScope>({ grade: v => Number.isInteger(v) && (v as number) >= 5 && (v as number) <= 8, subject: text, publisher_code: text, publisher_name: text, stage: oneOf(['primary', 'junior_middle'] as const), revision_policy: oneOf(['publisher_latest'] as const), textbook_revision: oneOf(['publisher_latest'] as const), catalog_status: oneOf(['available', 'incomplete', 'unavailable', 'publisher_mismatch'] as const), catalog_verified_at: nullable(date), catalog_candidates: list(text, 30), books: list(isCurriculumBook, 4) })
const isCurriculumSubject = shape<CurriculumSubject>({ grade: gradeValue, subject: text, publisher_code: text, publisher_name: text, stage: oneOf(['primary', 'junior_middle'] as const), revision_policy: oneOf(['publisher_latest'] as const), textbook_revision: oneOf(['publisher_latest'] as const), catalog_status: oneOf(['available', 'incomplete', 'unavailable', 'publisher_mismatch'] as const), catalog_verified_at: nullable(date), catalog_candidates: list(text, 30), books: list(isCurriculumBook, 4), graph_ready: v => typeof v === 'boolean', status: oneOf(['building', 'ready'] as const), correct_count: count, incorrect_count: count, partial_count: count, scorable_count: count, score_percent: nullable(percentage) })
const isCurriculumCatalog = (value: unknown): value is CurriculumCatalog => record(value) && value.revision_policy === 'publisher_latest' && Array.isArray(value.grades) && value.grades.every(g => count(g)) && record(value.subjects) && Object.values(value.subjects).every(items => Array.isArray(items) && items.every(isCurriculumScope)) && text(value.notice)
const isStudentCurriculum = shape<StudentCurriculum>({ student_id: id, grade: nullable(gradeValue), stage: nullable(oneOf(['primary', 'junior_middle'] as const)), subjects: list(isCurriculumScope), notice: text })
export const isKnowledgeMap = shape<KnowledgeMap>({ student_id: id, grade: optional(nullable(gradeValue)), projection: shape({ pending: v => typeof v === 'boolean', projected_at: nullable(date) }), states: list(isKnowledgeState), chapters: list(isChapter), subjects: optional(list(isCurriculumSubject)), notice: text })
export { isCurriculumCatalog, isStudentCurriculum }
const isEvidence = shape<KnowledgeEvidence>({ id, occurred_at: date, question_text: nullable(text), homework_title: nullable(text), result: oneOf(evidenceResults), assistance_level: text, event_type: text })
export const isKnowledgeDetail = shape<KnowledgeDetail>({
  node: shape({ id, name: text, observable_objective: nullable(text), definition: nullable(text) }),
  state: nullable(shape({ learning_status: status, reason_codes: list(text, 20) })),
  evidence: list(isEvidence, 1000), prerequisites: list(isRelated), notice: text,
})
const isTip = shape<AgentTip>({ kind: oneOf(['past_difficulty', 'past_progress', 'agreed_support'] as const), text, memory_id: id, evidence_ids: list(id) })
export const isAgentContext = shape<AgentContext>({ case_id: id, tips: list(isTip, 50), knowledge: list(isRelated, 50), notice: text })
export const isMemory = shape<Memory>({ id, category: oneOf(memoryCategories), content: text, source: text, status: text, confirmed_by: nullable(text), valid_until: nullable(date), created_at: nullable(date) })
export const isMemoryList = shape<MemoryList>({ student_id: id, items: list(isMemory), notice: text })
const isVariant = shape<VariantOffer>({ knowledge_id: id, name: text, learning_status: status, available_items: count })
const isPreview = shape<PreviewOffer>({ knowledge_ids: list(id, 20), names: list(text, 20), quiz_items_available: count })
export const isOptionalActivities = shape<OptionalActivities>({ gate: shape({ open: v => typeof v === 'boolean', reasons: list(text, 20), remaining_minutes: count }), variants: list(isVariant, 100), preview: nullable(isPreview), notice: text })
const isVariantQuestion = shape<VariantErrorQuestion>({ case_id: id, question_id: id, question_text: nullable(text), homework_title: nullable(text), occurred_at: date, correct_answer: nullable(text), correction_method: nullable(text) })
const isVariantGroup = shape<VariantErrorGroup>({ knowledge_id: id, name: text, learning_status: status, error_count: count, source_case_id: id, available_items: count, questions: list(isVariantQuestion, 50) })
export const isVariantPracticeOverview = shape<VariantPracticeOverview>({
  student_id: id, projection: shape({ pending: v => typeof v === 'boolean', projected_at: nullable(date) }),
  gate: shape({ open: v => typeof v === 'boolean', reasons: list(text, 20), remaining_minutes: count }),
  groups: list(isVariantGroup, 100), pending_count: count, notice: text,
})
const isItem = shape<PracticeItem>({
  id, knowledge_id: id, stem_text: text, answer_kind: oneOf(['number', 'choice', 'text'] as const), has_hint: v => typeof v === 'boolean',
  response: optional(shape({ answer_text: text, result: oneOf(practiceResults), hint_used: v => typeof v === 'boolean' })), solution_text: optional(text),
})
const isTarget = shape<PreviewTarget>({ id, name: text, objective: nullable(text), prerequisites: list(isRelated, 50) })
export const isPracticeSession = shape<PracticeSession>({
  id, student_id: id, purpose: oneOf(['variant', 'preview'] as const), knowledge_ids: list(id, 20), status: oneOf(practiceStatuses), exit_reason: nullable(oneOf(exitReasons)),
  items: list(isItem, 20), started_at: date, budget_ends_at: date, ended_at: nullable(date), hints_allowed: v => typeof v === 'boolean', notice: text, targets: optional(list(isTarget, 20)),
})
export const isPracticeSessionList = shape<PracticeSessionList>({ items: list(isPracticeSession, 100) })
export const isPracticeAnswer = shape<PracticeAnswer>({ item_id: id, result: oneOf(practiceResults), solution_text: text, session_status: oneOf(practiceStatuses) })
export const isPracticeHint = shape<PracticeHint>({ item_id: id, hint_text: text })
