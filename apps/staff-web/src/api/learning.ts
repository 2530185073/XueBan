import { ApiError, type StaffApi } from './client'
import {
  isAgentRun, isAgentRunList, isAttemptCondition, isConditionRead, isCreatedDiagnosis, isCreatedShareLink, isCurriculumGraph, isGate, isGenerateAccepted, isKnowledgeDetail,
  isKnowledgeLink, isKnowledgeLinkList, isLearningProfile, isLinkQueue, isMemory, isMemoryList, isOptionalActivities, isPracticeItem, isPracticeItemList,
  isPracticeSessionList, isReport, isReportList, isReportQueue, isRetractedDiagnosis, isShareLink, isShareLinkList, isStudentKnowledge,
  type AssistanceInput, type CurriculumGraph, type DiagnosisInput, type ItemEdits, type ItemInput, type ItemStatus, type LinkDecision, type MemoryInput,
  type ProfileInput, type ReportStatus, type WindowInput,
} from './learningTypes'

const segment = encodeURIComponent
const invalid = () => new ApiError(422, 'Invalid request', 'invalid_request')
const PERIOD_START = /^\d{4}-\d{2}-(01|16)$/

/** Knowledge, memory, report and practice endpoints, on the same session and pending-write lock as the workbench. */
export class LearningApi {
  private graphRead: Promise<CurriculumGraph> | null = null

  constructor(private readonly api: StaffApi) {}

  /** The published graph changes rarely; one read is shared until it fails. */
  graph(): Promise<CurriculumGraph> {
    this.graphRead ??= this.api.call('/v1/curriculum/graph', isCurriculumGraph).catch(error => { this.graphRead = null; throw error })
    return this.graphRead
  }

  linkQueue(signal?: AbortSignal) { return this.api.call('/v1/knowledge-link-queue?limit=100', isLinkQueue, { signal }) }
  links(questionId: string, signal?: AbortSignal) { return this.api.call(`/v1/questions/${segment(questionId)}/knowledge-links`, isKnowledgeLinkList, { signal }) }
  decideLink(questionId: string, payload: LinkDecision) {
    if (payload.rationale && payload.rationale.length > 500) throw invalid()
    return this.api.call(`/v1/questions/${segment(questionId)}/knowledge-links`, isKnowledgeLink, { payload })
  }
  condition(attemptId: string, signal?: AbortSignal) { return this.api.call(`/v1/attempts/${segment(attemptId)}/condition`, isConditionRead, { signal }) }
  recordCondition(attemptId: string, payload: { assistance_level: AssistanceInput; expected_condition_id: string | null; note: string | null }) {
    return this.api.call(`/v1/attempts/${segment(attemptId)}/condition`, isAttemptCondition, { payload })
  }

  knowledge(studentId: string, signal?: AbortSignal) { return this.api.call(`/v1/students/${segment(studentId)}/knowledge`, isStudentKnowledge, { signal }) }
  knowledgeDetail(studentId: string, knowledgeId: string, signal?: AbortSignal) {
    return this.api.call(`/v1/students/${segment(studentId)}/knowledge/${segment(knowledgeId)}?include_invalidated=true`, isKnowledgeDetail, { signal })
  }
  addDiagnosis(studentId: string, knowledgeId: string, payload: DiagnosisInput) {
    return this.api.call(`/v1/students/${segment(studentId)}/knowledge/${segment(knowledgeId)}/diagnoses`, isCreatedDiagnosis, { payload })
  }
  retractDiagnosis(id: string) { return this.api.call(`/v1/diagnoses/${segment(id)}/retract`, isRetractedDiagnosis, { payload: {} }) }
  profile(studentId: string, signal?: AbortSignal) { return this.api.call(`/v1/students/${segment(studentId)}/learning-profile`, isLearningProfile, { signal }) }
  saveProfile(studentId: string, payload: ProfileInput) {
    return this.api.call(`/v1/students/${segment(studentId)}/learning-profile`, isLearningProfile, { payload, method: 'PUT' })
  }

  memories(studentId: string, signal?: AbortSignal) { return this.api.call(`/v1/students/${segment(studentId)}/memories?include_inactive=true`, isMemoryList, { signal }) }
  createMemory(studentId: string, payload: MemoryInput) {
    if (payload.content.trim().length < 2 || payload.content.length > 120) throw invalid()
    return this.api.call(`/v1/students/${segment(studentId)}/memories`, isMemory, { payload })
  }
  confirmMemory(id: string) { return this.api.call(`/v1/memories/${segment(id)}/confirm`, isMemory, { payload: {} }) }
  retractMemory(id: string) { return this.api.call(`/v1/memories/${segment(id)}/retract`, isMemory, { payload: {} }) }
  requestSummary(studentId: string) { return this.api.call(`/v1/students/${segment(studentId)}/agent-runs`, isAgentRun, { payload: { purpose: 'staff_summary' } }) }
  runs(studentId: string, signal?: AbortSignal) { return this.api.call(`/v1/students/${segment(studentId)}/agent-runs`, isAgentRunList, { signal }) }

  reportQueue(status: Exclude<ReportStatus, 'superseded'>, signal?: AbortSignal) { return this.api.call(`/v1/reports?status=${status}`, isReportQueue, { signal }) }
  studentReports(studentId: string, signal?: AbortSignal) { return this.api.call(`/v1/students/${segment(studentId)}/reports`, isReportList, { signal }) }
  generateReport(studentId: string, periodStart: string) {
    if (!PERIOD_START.test(periodStart)) throw invalid()
    return this.api.call(`/v1/students/${segment(studentId)}/reports`, isReport, { payload: { period_start: periodStart } })
  }
  report(id: string, signal?: AbortSignal) { return this.api.call(`/v1/reports/${segment(id)}`, isReport, { signal }) }
  editReport(id: string, payload: { expected_revision: number; staff_note: string | null; next_step: string | null }) {
    return this.api.call(`/v1/reports/${segment(id)}`, isReport, { payload, method: 'PATCH' })
  }
  publishReport(id: string, payload: { expected_revision: number; correction_note: string | null }) {
    return this.api.call(`/v1/reports/${segment(id)}/publish`, isReport, { payload })
  }
  createShareLink(reportId: string, expiresInDays: number) {
    if (!Number.isInteger(expiresInDays) || expiresInDays < 1 || expiresInDays > 90) throw invalid()
    return this.api.call(`/v1/reports/${segment(reportId)}/share-links`, isCreatedShareLink, { payload: { expires_in_days: expiresInDays } })
  }
  shareLinks(reportId: string, signal?: AbortSignal) { return this.api.call(`/v1/reports/${segment(reportId)}/share-links`, isShareLinkList, { signal }) }
  revokeShareLink(id: string) { return this.api.call(`/v1/report-share-links/${segment(id)}/revoke`, isShareLink, { payload: {} }) }

  practiceItems(status: Extract<ItemStatus, 'candidate' | 'approved'>, knowledgeId: string | null, signal?: AbortSignal) {
    const params = new URLSearchParams({ status }); if (knowledgeId) params.set('knowledge_id', knowledgeId)
    return this.api.call(`/v1/practice-items?${params}`, isPracticeItemList, { signal })
  }
  reviewItem(id: string, payload: { action: 'approve' | 'reject' | 'retire'; expected_revision: number; edits?: ItemEdits }) {
    return this.api.call(`/v1/practice-items/${segment(id)}/review`, isPracticeItem, { payload })
  }
  createItem(payload: ItemInput) { return this.api.call('/v1/practice-items', isPracticeItem, { payload }) }
  generateItems(payload: { knowledge_id: string; count: number; source_case_id?: string }) {
    if (!Number.isInteger(payload.count) || payload.count < 1 || payload.count > 5) throw invalid()
    return this.api.call('/v1/practice-items/generate', isGenerateAccepted, { payload })
  }
  setWindow(studentId: string, payload: WindowInput) {
    if (!Number.isInteger(payload.extra_minutes) || payload.extra_minutes < 0 || payload.extra_minutes > 60) throw invalid()
    return this.api.call(`/v1/students/${segment(studentId)}/optional-window`, isGate, { payload, method: 'PUT' })
  }
  activities(studentId: string, signal?: AbortSignal) { return this.api.call(`/v1/students/${segment(studentId)}/optional-activities`, isOptionalActivities, { signal }) }
  sessions(studentId: string, signal?: AbortSignal) { return this.api.call(`/v1/students/${segment(studentId)}/practice-sessions`, isPracticeSessionList, { signal }) }
}
