// Later-phase learning DTOs (knowledge, memory, reports, practice). Not in contracts/phase1; validated by hand here.

const record = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value)
const str = (value: unknown, max = 20000): value is string => typeof value === 'string' && value.length <= max
const text = (value: unknown, max = 20000): value is string => str(value, max) && value.length > 0
const id = (value: unknown): value is string => typeof value === 'string' && /^[A-Za-z0-9][A-Za-z0-9_.:-]{1,127}$/.test(value)
const time = (value: unknown) => typeof value === 'string' && Number.isFinite(Date.parse(value))
const day = (value: unknown) => typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value)
const count = (value: unknown): value is number => Number.isInteger(value) && (value as number) >= 0
const nullable = <T>(check: (value: unknown) => boolean) => (value: unknown): value is T | null => value === null || check(value)
const optStr = nullable<string>(value => str(value))
const optTime = nullable<string>(time)
const list = <T>(value: unknown, check: (item: unknown) => boolean, max = 2000): value is T[] => Array.isArray(value) && value.length <= max && value.every(check)
const ids = (value: unknown, max = 200): value is string[] => list<string>(value, id, max)
const oneOf = <T extends string>(values: readonly T[]) => (value: unknown): value is T => values.includes(value as T)
const counts = (value: unknown): value is Record<string, number> => record(value) && Object.values(value).every(count)

export const LEARNING_STATUSES = ['needs_help_now', 'consolidating', 'stable_for_now', 'no_judgment'] as const
export type LearningStatus = typeof LEARNING_STATUSES[number]
const status = oneOf(LEARNING_STATUSES)

// Curriculum graph

export interface KnowledgeNode { id: string; node_type: string; parent_id: string | null; name: string; definition: string | null; observable_objective: string | null; sort_order: number }
export interface CurriculumGraph { graph_version: string; title: string; review_status: string; nodes: KnowledgeNode[] }

export function isKnowledgeNode(value: unknown): value is KnowledgeNode {
  return record(value) && id(value.id) && text(value.node_type, 16) && optStr(value.parent_id) && text(value.name, 200) &&
    optStr(value.definition) && optStr(value.observable_objective) && typeof value.sort_order === 'number'
}
export function isCurriculumGraph(value: unknown): value is CurriculumGraph {
  return record(value) && text(value.graph_version, 64) && str(value.title, 200) && str(value.review_status, 64) && list(value.nodes, isKnowledgeNode, 5000)
}

// Question knowledge links

export type LinkRole = 'primary' | 'secondary'
export type LinkStatus = 'candidate' | 'confirmed' | 'rejected'
export interface KnowledgeLink {
  id: string; question_id: string; knowledge_id: string; knowledge_name: string | null; role: LinkRole; source: string; status: LinkStatus
  rationale: string | null; actor_id: string | null; supersedes_id: string | null; created_at: string
}
export interface KnowledgeLinkList { question_id: string; question_revision: number; graph_version: string; items: KnowledgeLink[] }
export interface LinkQueueItem { case_id: string; question_id: string; student_id: string; homework_title: string | null; question_text: string | null; verdict: string; candidates: KnowledgeLink[] }
export interface LinkQueue { items: LinkQueueItem[] }
export interface LinkDecision { knowledge_id: string; role: LinkRole; status: 'confirmed' | 'rejected'; expected_link_id: string | null; rationale: string | null; expected_question_revision: number; expected_graph_version: string }

export function isKnowledgeLink(value: unknown): value is KnowledgeLink {
  return record(value) && id(value.id) && id(value.question_id) && id(value.knowledge_id) && optStr(value.knowledge_name) &&
    oneOf<LinkRole>(['primary', 'secondary'])(value.role) && text(value.source, 32) && oneOf<LinkStatus>(['candidate', 'confirmed', 'rejected'])(value.status) &&
    optStr(value.rationale) && optStr(value.actor_id) && optStr(value.supersedes_id) && time(value.created_at)
}
export function isKnowledgeLinkList(value: unknown): value is KnowledgeLinkList {
  return record(value) && id(value.question_id) && Number.isInteger(value.question_revision) && (value.question_revision as number) >= 1 && text(value.graph_version, 64) && list(value.items, isKnowledgeLink, 200)
}
export function isLinkQueue(value: unknown): value is LinkQueue {
  return record(value) && list(value.items, item => record(item) && id(item.case_id) && id(item.question_id) && id(item.student_id) &&
    optStr(item.homework_title) && optStr(item.question_text) && text(item.verdict, 32) && list(item.candidates, isKnowledgeLink, 200), 100)
}

// Attempt help condition

export const ASSISTANCE_INPUTS = ['independent', 'direction', 'key_step', 'staff_help', 'solution_seen', 'unknown'] as const
export type AssistanceInput = typeof ASSISTANCE_INPUTS[number]
export interface AttemptCondition { id: string; attempt_id: string; assistance_level: AssistanceInput; note: string | null; created_at: string }
export function isAttemptCondition(value: unknown): value is AttemptCondition {
  return record(value) && id(value.id) && id(value.attempt_id) && oneOf(ASSISTANCE_INPUTS)(value.assistance_level) && optStr(value.note) && time(value.created_at)
}
export interface ConditionRead { attempt_id: string; current: AttemptCondition | null }
export function isConditionRead(value: unknown): value is ConditionRead {
  return record(value) && id(value.attempt_id) && (value.current === null || isAttemptCondition(value.current))
}

// Student knowledge

export interface Diagnosis { id: string; kind: 'suspected_cause' | 'verified_weak_scope'; scope_text: string; evidence_ids: string[] }
export interface OpenQuestion { code: string; count: number }
export interface EvidenceSources {
  model_evidence_count?: number; staff_verified_evidence_count?: number
  assessment_source?: 'model' | 'staff' | 'mixed' | 'none'
}
const isEvidenceSources = (value: Record<string, unknown>) =>
  (value.model_evidence_count === undefined || count(value.model_evidence_count)) &&
  (value.staff_verified_evidence_count === undefined || count(value.staff_verified_evidence_count)) &&
  (value.assessment_source === undefined || oneOf(['model', 'staff', 'mixed', 'none'] as const)(value.assessment_source))
type EvidenceSourceRole = 'model' | 'sitting_teacher' | 'unknown'
const isSourceRole = (value: unknown) => value === undefined || oneOf(['model', 'sitting_teacher', 'unknown'] as const)(value)
export interface KnowledgeState extends EvidenceSources {
  knowledge_id: string; learning_status: LearningStatus; reason_codes: string[]; evidence_sufficiency: string; sufficiency_explanation: string
  valid_evidence_count: number; scorable_evidence_count: number; independent_evidence_count: number; independent_dates_count: number; observed_error_count: number
  last_error_at: string | null; last_independent_success_at: string | null; review_due_at: string | null
  diagnoses: Diagnosis[]; open_questions: OpenQuestion[]; evidence_ids: string[]
}
export interface NamedKnowledgeState extends KnowledgeState { name: string; chapter_id: string | null; chapter_name: string | null }
export interface ChapterRollup { chapter_id: string; name: string; objectives: number; status_counts: Partial<Record<LearningStatus, number>> }
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
export interface StudentKnowledge {
  mapping_coverage?: MappingCoverage
  student_id: string; graph_version: string | null; graph_review_status: string
  projection: { pending: boolean; projected_at: string | null; state_version: number }
  states: NamedKnowledgeState[]; chapters: ChapterRollup[]; notice: string
}

const isDiagnosis = (value: unknown): value is Diagnosis => record(value) && id(value.id) && oneOf(['suspected_cause', 'verified_weak_scope'] as const)(value.kind) &&
  text(value.scope_text, 500) && ids(value.evidence_ids)
export function isKnowledgeState(value: unknown): value is KnowledgeState {
  return record(value) && isEvidenceSources(value) && id(value.knowledge_id) && status(value.learning_status) && list(value.reason_codes, item => text(item, 64), 20) &&
    text(value.evidence_sufficiency, 32) && str(value.sufficiency_explanation, 500) &&
    [value.valid_evidence_count, value.scorable_evidence_count, value.independent_evidence_count, value.independent_dates_count, value.observed_error_count].every(count) &&
    optTime(value.last_error_at) && optTime(value.last_independent_success_at) && optTime(value.review_due_at) &&
    list(value.diagnoses, isDiagnosis, 100) && list(value.open_questions, item => record(item) && text(item.code, 64) && count(item.count), 20) && ids(value.evidence_ids, 5000)
}
const isNamedState = (value: unknown): value is NamedKnowledgeState => isKnowledgeState(value) && record(value) && text(value.name, 200) && optStr(value.chapter_id) && optStr(value.chapter_name)
const isRollup = (value: unknown): value is ChapterRollup => record(value) && id(value.chapter_id) && text(value.name, 200) && count(value.objectives) &&
  record(value.status_counts) && Object.entries(value.status_counts).every(([key, n]) => status(key) && count(n))
export function isMappingCoverage(value: unknown): value is MappingCoverage {
  return record(value) && value.scope === 'current_reliable_errors_in_selected_graph' && value.time_basis === 'current_attempt_occurred_at'
    && optStr(value.graph_version) && record(value.window) && optTime(value.window.start) && optTime(value.window.end_exclusive)
    && typeof value.available === 'boolean' && [value.total, value.linked, value.unlinked, value.model_count, value.staff_count].every(count)
    && value.total === (value.linked as number) + (value.unlinked as number) && value.total === (value.model_count as number) + (value.staff_count as number)
    && typeof value.truncated === 'boolean' && text(value.notice, 1000)
    && list(value.items, item => record(item) && id(item.case_id) && id(item.question_id) && id(item.homework_id)
      && str(item.label, 80) && text(item.subject, 40) && str(item.stem_preview, 180) && time(item.occurred_at)
      && oneOf(['model', 'staff'] as const)(item.source_role)
      && (item.state === undefined || oneOf(['no_direct_match', 'mapping_pending', 'not_linked'] as const)(item.state)), 30)
    && (value.items as unknown[]).length <= (value.unlinked as number)
    && value.truncated === ((value.unlinked as number) > (value.items as unknown[]).length)
}
export function isStudentKnowledge(value: unknown): value is StudentKnowledge {
  return record(value) && id(value.student_id) && (value.graph_version === null || text(value.graph_version, 64)) && str(value.graph_review_status, 64) &&
    record(value.projection) && typeof value.projection.pending === 'boolean' && optTime(value.projection.projected_at) && count(value.projection.state_version) &&
    list(value.states, isNamedState, 2000) && list(value.chapters, isRollup, 500) && str(value.notice, 500)
    && (value.mapping_coverage === undefined || isMappingCoverage(value.mapping_coverage))
}

export interface Evidence {
  id: string; attempt_id: string; case_id: string; homework_id: string; question_id: string; verdict: string; event_type: string; result: string
  assistance_level: string; independent: boolean; link_role: LinkRole; occurred_at: string; validity: 'valid' | 'invalidated'
  invalidation_reason: string | null; question_text: string | null; homework_title: string | null; source_role?: EvidenceSourceRole
}
export interface RelatedNode { id: string; name: string; learning_status: LearningStatus }
export interface KnowledgeDetail { node: KnowledgeNode; state: KnowledgeState | null; evidence: Evidence[]; prerequisites: RelatedNode[]; leads_to: RelatedNode[]; notice: string }

export function isEvidence(value: unknown): value is Evidence {
  return record(value) && id(value.id) && id(value.attempt_id) && id(value.case_id) && id(value.homework_id) && id(value.question_id) &&
    text(value.verdict, 32) && text(value.event_type, 32) && text(value.result, 32) && text(value.assistance_level, 32) && typeof value.independent === 'boolean' &&
    oneOf<LinkRole>(['primary', 'secondary'])(value.link_role) && time(value.occurred_at) && oneOf(['valid', 'invalidated'] as const)(value.validity) &&
    optStr(value.invalidation_reason) && optStr(value.question_text) && optStr(value.homework_title) && isSourceRole(value.source_role)
}
const isRelated = (value: unknown): value is RelatedNode => record(value) && id(value.id) && text(value.name, 200) && status(value.learning_status)
export function isKnowledgeDetail(value: unknown): value is KnowledgeDetail {
  return record(value) && isKnowledgeNode(value.node) && (value.state === null || isKnowledgeState(value.state)) && list(value.evidence, isEvidence, 5000) &&
    list(value.prerequisites, isRelated, 100) && list(value.leads_to, isRelated, 100) && str(value.notice, 500)
}

export interface DiagnosisInput { kind: Diagnosis['kind']; scope_text: string; evidence_ids: string[] }
export interface CreatedDiagnosis extends Diagnosis { knowledge_id: string; status: string }
export function isCreatedDiagnosis(value: unknown): value is CreatedDiagnosis {
  return isDiagnosis(value) && record(value) && id(value.knowledge_id) && text(value.status, 16)
}
export function isRetractedDiagnosis(value: unknown): value is { id: string; status: 'retracted' } {
  return record(value) && id(value.id) && value.status === 'retracted'
}

// Learning profile

export interface LearningProfile {
  student_id: string; revision: number; grade: number | null; term: 'upper' | 'lower' | null; textbook_revision: string | null
  current_knowledge_ids: string[]; note: string | null; updated_at: string | null
}
export type ProfileInput = Omit<LearningProfile, 'student_id' | 'revision' | 'updated_at'> & { expected_revision: number }
export function isLearningProfile(value: unknown): value is LearningProfile {
  return record(value) && id(value.student_id) && count(value.revision) && (value.grade === null || (Number.isInteger(value.grade) && (value.grade as number) >= 1 && (value.grade as number) <= 12)) &&
    (value.term === null || value.term === 'upper' || value.term === 'lower') && optStr(value.textbook_revision) && ids(value.current_knowledge_ids, 20) &&
    optStr(value.note) && optTime(value.updated_at)
}

// Agent memories and runs

export const STAFF_MEMORY_CATEGORIES = ['effective_support', 'support_agreement', 'learning_goal', 'recurring_difficulty'] as const
export type StaffMemoryCategory = typeof STAFF_MEMORY_CATEGORIES[number]
export type MemoryStatus = 'proposed' | 'confirmed' | 'expired' | 'superseded' | 'invalidated' | 'retracted'
export interface Memory {
  id: string; category: string; content: string; knowledge_id: string | null; evidence_ids: string[]; source: 'system_rule' | 'model' | 'staff' | 'student'
  status: MemoryStatus; status_reason: string | null; confirmed_at: string | null; valid_until: string | null; supersedes_id: string | null; revision: number; created_at: string
}
export interface MemoryList { student_id: string; items: Memory[]; notice: string }
export interface MemoryInput { category: StaffMemoryCategory; content: string; knowledge_id: string | null; evidence_ids: string[] }

export function isMemory(value: unknown): value is Memory {
  return record(value) && id(value.id) && text(value.category, 32) && text(value.content, 500) && optStr(value.knowledge_id) && ids(value.evidence_ids, 50) &&
    oneOf(['system_rule', 'model', 'staff', 'student'] as const)(value.source) &&
    oneOf<MemoryStatus>(['proposed', 'confirmed', 'expired', 'superseded', 'invalidated', 'retracted'])(value.status) &&
    optStr(value.status_reason) && optTime(value.confirmed_at) && optTime(value.valid_until) && optStr(value.supersedes_id) && count(value.revision) && time(value.created_at)
}
export function isMemoryList(value: unknown): value is MemoryList {
  return record(value) && id(value.student_id) && list(value.items, isMemory, 2000) && str(value.notice, 500)
}

export type Certainty = 'low' | 'medium' | 'high'
export type RunStatus = 'queued' | 'completed' | 'needs_human' | 'stale' | 'failed'
export interface SummaryClaim { text: string; evidence_ids: string[]; certainty: Certainty }
export interface SummaryOutput { status: 'ready' | 'needs_evidence' | 'needs_human'; summary: string; claims: SummaryClaim[]; next_action: string }
export interface AgentRun { id: string; status: RunStatus; context: { evidence_ids?: string[] }; output: SummaryOutput | null; failure_code: string | null; created_at: string; completed_at: string | null }
export interface AgentRunList { items: AgentRun[] }

const isClaim = (value: unknown): value is SummaryClaim => record(value) && text(value.text, 400) && ids(value.evidence_ids, 20) && oneOf<Certainty>(['low', 'medium', 'high'])(value.certainty)
const isOutput = (value: unknown): value is SummaryOutput => record(value) && oneOf(['ready', 'needs_evidence', 'needs_human'] as const)(value.status) &&
  text(value.summary, 600) && list(value.claims, isClaim, 20) && str(value.next_action, 400)
export function isAgentRun(value: unknown): value is AgentRun {
  return record(value) && id(value.id) && oneOf<RunStatus>(['queued', 'completed', 'needs_human', 'stale', 'failed'])(value.status) &&
    record(value.context) && (value.context.evidence_ids === undefined || ids(value.context.evidence_ids, 200)) &&
    (value.output === null || isOutput(value.output)) && optStr(value.failure_code) && time(value.created_at) && optTime(value.completed_at)
}
export function isAgentRunList(value: unknown): value is AgentRunList {
  return record(value) && list(value.items, isAgentRun, 50)
}
/** Older runs carry an empty context until the worker fills it in. */
export const runEvidence = (run: AgentRun) => run.context.evidence_ids ?? []

// Half-month reports and daily feedback

export type ReportStatus = 'draft' | 'published' | 'superseded'
export type ReportCadence = 'half_month' | 'daily'
export interface DailyMistake { case_id: string; homework_title: string | null; page: number | null; question: string; verdict: string | null; progress: string; correction: string; source_role?: EvidenceSourceRole | null; certainty?: string }
export interface EvidenceCard { evidence_id: string; date: string; homework_title: string | null; page: number | null; question: string; result: string; assistance: string; staff_explanation: string | null; certainty: string; source_role?: EvidenceSourceRole }
export interface FocusItem extends EvidenceSources {
  knowledge_id: string; name: string; errors: number; denominator: number; independent_errors: number; counter_evidence: number; assistance: Record<string, number>
  status_at_period_end: LearningStatus; status_label: string; verified_scope: string[]; possible_causes: string[]; unknown: string[]; cards: EvidenceCard[]; next_step: string
}
export interface ReportSnapshot {
  mapping_coverage?: MappingCoverage
  period: { start: string; end_exclusive: string }
  coverage: { observed_days: number; observed_dates: string[]; homeworks: number; partial_period: boolean; first_seen_date: string | null }
  school_tasks: { questions: number; staff_verified: number; awaiting_verification: number; not_judged: number; verdicts: Record<'correct' | 'incorrect' | 'partially_correct' | 'unanswered', number>; corrected_resolved: number; correction_open: number; ai_judged?: number; ai_corrected_pending?: number; ai_verdicts?: Record<'correct' | 'incorrect' | 'partially_correct' | 'unanswered', number> }
  help: { requests: number; by_kind: Record<string, number>; resolved: number; hints_viewed: number; answers_shown: number; staff_help: { date: string; kind: string; question: string; outcome: string; note: string }[] }
  focus: FocusItem[]; steady: (EvidenceSources & { knowledge_id: string; name: string; correct: number; independent_correct: number })[]; unknown_current: { knowledge_id: string; name: string }[]
  sufficient: boolean; sections: { optional_activity?: { summary: string; answered?: number; correct?: number } }; limitations: string
  mistakes?: DailyMistake[]; completions?: DailyMistake[]; activity_kinds?: { preview: number; variant: number }
}
export interface Report {
  id: string; student_id: string; cadence: ReportCadence; period_start: string; period_end: string; version: number; status: ReportStatus; snapshot: ReportSnapshot | null
  staff_note: string | null; next_step: string | null; correction_note: string | null; supersedes_id: string | null; published_at: string | null
  revision: number; regenerated_reason: string | null; updated_at: string | null
}
export interface Delivery { links: number; copied: boolean; sent_confirmed_at: string | null; opened_at: string | null }
export interface QueuedReport extends Report { focus_count: number; mistake_count: number; sufficient: boolean; student_name: string | null; delivery: Delivery | null }
const isDelivery = (value: unknown): value is Delivery => record(value) && count(value.links) && typeof value.copied === 'boolean' && optTime(value.sent_confirmed_at) && optTime(value.opened_at)

const isCard = (value: unknown): value is EvidenceCard => record(value) && id(value.evidence_id) && day(value.date) && optStr(value.homework_title) &&
  (value.page === null || count(value.page)) && str(value.question, 500) && text(value.result, 32) && text(value.assistance, 64) && optStr(value.staff_explanation) && text(value.certainty, 32) && isSourceRole(value.source_role)
const isMistake = (value: unknown): value is DailyMistake => record(value) && id(value.case_id) && optStr(value.homework_title) &&
  (value.page === null || count(value.page)) && str(value.question, 500) && optStr(value.verdict) && text(value.progress, 32) && text(value.correction, 64) &&
  (value.source_role === null || isSourceRole(value.source_role)) && (value.certainty === undefined || text(value.certainty, 64))
const isFocus = (value: unknown): value is FocusItem => record(value) && isEvidenceSources(value) && id(value.knowledge_id) && text(value.name, 200) &&
  [value.errors, value.denominator, value.independent_errors, value.counter_evidence].every(count) && counts(value.assistance) && status(value.status_at_period_end) &&
  text(value.status_label, 32) && list(value.verified_scope, item => text(item, 500), 50) && list(value.possible_causes, item => text(item, 500), 50) &&
  list(value.unknown, item => text(item, 64), 20) && list(value.cards, isCard, 20) && text(value.next_step, 500)
export function isReportSnapshot(value: unknown): value is ReportSnapshot {
  if (!record(value) || !record(value.period) || !record(value.coverage) || !record(value.school_tasks) || !record(value.help) || !record(value.sections)) return false
  const { period, coverage, school_tasks: tasks, help, sections } = value
  const verdicts = tasks.verdicts
  return day(period.start) && day(period.end_exclusive) &&
    (value.mapping_coverage === undefined || isMappingCoverage(value.mapping_coverage)) &&
    count(coverage.observed_days) && list(coverage.observed_dates, day, 31) && count(coverage.homeworks) && typeof coverage.partial_period === 'boolean' && nullable(day)(coverage.first_seen_date) &&
    [tasks.questions, tasks.staff_verified, tasks.awaiting_verification, tasks.not_judged, tasks.corrected_resolved, tasks.correction_open].every(count) &&
    record(verdicts) && ['correct', 'incorrect', 'partially_correct', 'unanswered'].every(key => count(verdicts[key])) &&
    (tasks.ai_judged === undefined || count(tasks.ai_judged)) && (tasks.ai_corrected_pending === undefined || count(tasks.ai_corrected_pending)) &&
    (tasks.ai_verdicts === undefined || (record(tasks.ai_verdicts) && ['correct', 'incorrect', 'partially_correct', 'unanswered'].every(key => count((tasks.ai_verdicts as Record<string, unknown>)[key])))) &&
    count(help.requests) && counts(help.by_kind) && count(help.resolved) && count(help.hints_viewed) && count(help.answers_shown) &&
    list(help.staff_help, item => record(item) && day(item.date) && text(item.kind, 32) && str(item.question, 200) && text(item.outcome, 32) && str(item.note, 500), 20) &&
    list(value.focus, isFocus, 500) && list(value.steady, item => record(item) && isEvidenceSources(item) && id(item.knowledge_id) && text(item.name, 200) && count(item.correct) && count(item.independent_correct), 500) &&
    list(value.unknown_current, item => record(item) && id(item.knowledge_id) && text(item.name, 200), 50) && typeof value.sufficient === 'boolean' &&
    (sections.optional_activity === undefined || (record(sections.optional_activity) && text(sections.optional_activity.summary, 500))) && text(value.limitations, 2000) &&
    (value.mistakes === undefined || list(value.mistakes, isMistake, 30)) &&
    (value.completions === undefined || list(value.completions, isMistake, 30)) &&
    (value.activity_kinds === undefined || (record(value.activity_kinds) && count(value.activity_kinds.preview) && count(value.activity_kinds.variant)))
}
function isReportBase(value: unknown): value is Report {
  return record(value) && id(value.id) && id(value.student_id) && oneOf<ReportCadence>(['half_month', 'daily'])(value.cadence) && day(value.period_start) && day(value.period_end) && count(value.version) && (value.version as number) >= 1 &&
    oneOf<ReportStatus>(['draft', 'published', 'superseded'])(value.status) && optStr(value.staff_note) && optStr(value.next_step) && optStr(value.correction_note) &&
    optStr(value.supersedes_id) && optTime(value.published_at) && count(value.revision) && optStr(value.regenerated_reason) && optTime(value.updated_at)
}
export function isReport(value: unknown): value is Report {
  return isReportBase(value) && isReportSnapshot(value.snapshot)
}
export function isReportList(value: unknown): value is { items: Report[] } {
  return record(value) && list(value.items, isReport, 500)
}
export function isReportQueue(value: unknown): value is { items: QueuedReport[] } {
  return record(value) && list(value.items, item => {
    const extra = item as unknown as Record<string, unknown>
    return isReportBase(item) && item.snapshot === null && count(extra.focus_count) && count(extra.mistake_count) && typeof extra.sufficient === 'boolean' && optStr(extra.student_name) &&
      (extra.delivery == null || isDelivery(extra.delivery))
  }, 200) && (value.next_offset === null || count(value.next_offset))
}

export interface ShareLink {
  id: string; report_id: string; created_at: string; expires_at: string; revoked_at: string | null; view_count: number; last_viewed_at: string | null
  message_copied_at?: string | null; sent_confirmed_at?: string | null; sent_confirmed_by?: string | null; sent_note?: string | null
}
export interface CreatedShareLink extends ShareLink { token: string; path: string }
export function isShareLink(value: unknown): value is ShareLink {
  return record(value) && id(value.id) && id(value.report_id) && time(value.created_at) && time(value.expires_at) && optTime(value.revoked_at) && count(value.view_count) && optTime(value.last_viewed_at) &&
    [value.message_copied_at, value.sent_confirmed_at].every(item => item === undefined || optTime(item)) &&
    [value.sent_confirmed_by, value.sent_note].every(item => item === undefined || optStr(item))
}
export function isCreatedShareLink(value: unknown): value is CreatedShareLink {
  return isShareLink(value) && record(value) && typeof value.token === 'string' && /^[A-Za-z0-9_-]{20,128}$/.test(value.token) && value.path === `/r/${value.token}`
}
export function isShareLinkList(value: unknown): value is { items: ShareLink[] } {
  return record(value) && list(value.items, isShareLink, 500)
}

// Practice bank and optional window

export type AnswerKind = 'number' | 'choice' | 'text'
export type ItemStatus = 'candidate' | 'approved' | 'rejected' | 'retired'
export interface PracticeItem {
  id: string; knowledge_id: string; stem_text: string; answer_kind: AnswerKind; difficulty: number; item_family_id: string; answer_values: string[]
  hint_text: string | null; solution_text: string; source: string; status: ItemStatus; revision: number; reviewed_at: string | null; created_at: string
}
export interface ItemInput { knowledge_id: string; stem_text: string; answer_kind: AnswerKind; answer_values: string[]; hint_text: string | null; solution_text: string; difficulty: number }
export type ItemEdits = Partial<Pick<ItemInput, 'stem_text' | 'answer_values' | 'hint_text' | 'solution_text' | 'difficulty'>>

export function isPracticeItem(value: unknown): value is PracticeItem {
  return record(value) && id(value.id) && id(value.knowledge_id) && text(value.stem_text, 600) && oneOf<AnswerKind>(['number', 'choice', 'text'])(value.answer_kind) &&
    [1, 2, 3].includes(value.difficulty as number) && id(value.item_family_id) && list(value.answer_values, item => text(item, 60), 4) && optStr(value.hint_text) &&
    text(value.solution_text, 800) && text(value.source, 16) && oneOf<ItemStatus>(['candidate', 'approved', 'rejected', 'retired'])(value.status) &&
    count(value.revision) && optTime(value.reviewed_at) && time(value.created_at)
}
export function isPracticeItemList(value: unknown): value is { items: PracticeItem[] } {
  return record(value) && list(value.items, isPracticeItem, 200)
}
export function isGenerateAccepted(value: unknown): value is { job_id: string; status: string; notice: string } {
  return record(value) && id(value.job_id) && text(value.status, 16) && str(value.notice, 500)
}
export const GENERATION_STATUSES = ['queued', 'running', 'succeeded', 'failed'] as const
export interface GenerationStatus { job_id: string; status: typeof GENERATION_STATUSES[number]; failure_code: string | null; knowledge_id: string; candidate_item_ids: string[] }
export function isGenerationStatus(value: unknown): value is GenerationStatus {
  return record(value) && id(value.job_id) && oneOf(GENERATION_STATUSES)(value.status) && (value.failure_code === null || text(value.failure_code, 64))
    && id(value.knowledge_id) && Array.isArray(value.candidate_item_ids) && value.candidate_item_ids.every(id)
}

export type GateReason = 'school_tasks_not_confirmed' | 'capacity_not_observed' | 'no_extra_budget'
export interface Gate {
  open: boolean; reasons: GateReason[]
  school_tasks_complete: { value: boolean | null; source: 'system' | 'staff'; detail?: string; at?: string | null }
  has_capacity: { value: boolean | null; at: string | null }
  extra_minutes: number; remaining_minutes: number; window_revision: number; checked_at: string
}
export interface WindowInput { expected_revision: number; capacity_observed: boolean; extra_minutes: number; school_tasks_confirmed: boolean | null }
export interface OptionalActivities {
  gate: Gate; variants: { knowledge_id: string; name: string; learning_status: LearningStatus; available_items: number }[]
  preview: { knowledge_ids: string[]; names: string[]; quiz_items_available: number } | null; notice: string
}

const bool3 = (value: unknown) => value === null || typeof value === 'boolean'
export function isGate(value: unknown): value is Gate {
  return record(value) && typeof value.open === 'boolean' && list(value.reasons, oneOf<GateReason>(['school_tasks_not_confirmed', 'capacity_not_observed', 'no_extra_budget']), 3) &&
    record(value.school_tasks_complete) && bool3(value.school_tasks_complete.value) && oneOf(['system', 'staff'] as const)(value.school_tasks_complete.source) &&
    (value.school_tasks_complete.detail === undefined || str(value.school_tasks_complete.detail, 200)) &&
    record(value.has_capacity) && bool3(value.has_capacity.value) && optTime(value.has_capacity.at) &&
    count(value.extra_minutes) && count(value.remaining_minutes) && count(value.window_revision) && time(value.checked_at)
}
export function isOptionalActivities(value: unknown): value is OptionalActivities {
  return record(value) && isGate(value.gate) && str(value.notice, 500) &&
    list(value.variants, item => record(item) && id(item.knowledge_id) && text(item.name, 200) && status(item.learning_status) && count(item.available_items), 500) &&
    (value.preview === null || (record(value.preview) && ids(value.preview.knowledge_ids, 20) && list(value.preview.names, item => text(item, 200), 20) && count(value.preview.quiz_items_available)))
}

export interface PracticeSession {
  id: string; purpose: 'variant' | 'preview'; knowledge_ids: string[]; status: 'active' | 'completed' | 'exited' | 'time_up'; exit_reason: string | null
  items: { id: string; stem_text: string; response?: { answer_text: string; result: string; hint_used: boolean } }[]
  started_at: string; budget_ends_at: string; ended_at: string | null
}
const isSessionItem = (value: unknown) => record(value) && id(value.id) && text(value.stem_text, 600) &&
  (value.response === undefined || (record(value.response) && str(value.response.answer_text, 200) && text(value.response.result, 16) && typeof value.response.hint_used === 'boolean'))
export function isPracticeSessionList(value: unknown): value is { items: PracticeSession[] } {
  return record(value) && list(value.items, item => record(item) && id(item.id) && oneOf(['variant', 'preview'] as const)(item.purpose) && ids(item.knowledge_ids, 20) &&
    oneOf(['active', 'completed', 'exited', 'time_up'] as const)(item.status) && optStr(item.exit_reason) && list(item.items, isSessionItem, 10) &&
    time(item.started_at) && time(item.budget_ends_at) && optTime(item.ended_at), 50)
}
