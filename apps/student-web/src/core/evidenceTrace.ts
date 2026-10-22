import type { KnowledgeDetail, KnowledgeEvidence } from './learning'
import type { Attempt, CaseRecord, CapturedQuestionRevision, Decision, QuestionRead } from './types'
import type { StudentClient } from './student'

/** An evidence pointer, not a copy of the current answer or a mastery claim. */
export interface EvidenceTarget {
  knowledgeId: string; graphVersion: string | null; evidenceId: string
  homeworkId: string; caseId: string; questionId?: string; attemptId?: string; decisionId?: string
}
export interface EvidenceFocus extends EvidenceTarget { scope: string }
export interface EvidenceTrace { evidence: KnowledgeEvidence; attempt: Attempt; decision: Decision; question: CapturedQuestionRevision; record: CaseRecord }
export class EvidenceTraceUnavailable extends Error {}
const unavailable = () => { throw new EvidenceTraceUnavailable('这条学习记录已更新，或对应的旧版本暂时无法定位。没有用当前作答代替。') }

export function evidenceTarget(detail: KnowledgeDetail, e: KnowledgeEvidence): EvidenceTarget {
  return { knowledgeId: detail.node.id, graphVersion: detail.graph_version ?? null, evidenceId: e.id,
    homeworkId: e.homework_id!, caseId: e.case_id!, questionId: e.question_id, attemptId: e.attempt_id, decisionId: e.decision_id }
}
export function evidenceFocusKey(focus: EvidenceFocus) { return JSON.stringify(focus) }

/** Check the live projection, exact historical answer, and the judgment's reading version. */
export function resolveEvidence(focus: EvidenceTarget, studentId: string, detail: KnowledgeDetail, record: CaseRecord) {
  if (!focus.graphVersion || detail.graph_version !== focus.graphVersion || detail.node.id !== focus.knowledgeId || detail.projection?.pending) return unavailable()
  const matches = detail.evidence.filter(e => e.id === focus.evidenceId), e = matches[0], c = record.case
  if (matches.length !== 1 || !focus.attemptId || !focus.decisionId || !focus.questionId
    || e.homework_id !== focus.homeworkId || e.case_id !== focus.caseId || e.question_id !== focus.questionId
    || e.attempt_id !== focus.attemptId || e.decision_id !== focus.decisionId
    || c.lifecycle !== 'active' || c.id !== focus.caseId || c.homework_id !== focus.homeworkId
    || c.scope.student_id !== studentId || c.question_id !== focus.questionId) return unavailable()
  const attempts = record.attempts.filter(a => a.record.id === focus.attemptId), decisions = record.decisions.filter(d => d.record.id === focus.decisionId)
  const a = attempts[0]?.record, d = decisions[0]?.record
  if (attempts.length !== 1 || decisions.length !== 1 || !c.attempt_ids.includes(focus.attemptId)
    || a.case_id !== c.id || a.question_id !== c.question_id || a.root_attempt_id !== c.root_attempt_id
    || attempts[0].current_revision !== a.revision || decisions[0].current_revision !== d.revision
    || d.case_id !== c.id || d.input_versions.question_id !== c.question_id || d.input_versions.attempt_id !== a.id
    || d.input_versions.attempt_recognition_revision !== a.recognition_revision
    || d.input_versions.question_revision !== c.current_question_revision || d.validity === 'retracted') return unavailable()
  // A prior decision may be stale because a *new attempt* followed it. The live
  // evidence endpoint, not currently_effective, decides whether it still contributes.
  return { evidence: e, attempt: a, decision: d, record }
}

type TraceClient = Pick<StudentClient, 'evidenceScope' | 'knowledgeNode' | 'caseRecord' | 'questionRevision'>
export async function loadEvidenceTrace(client: TraceClient, focus: EvidenceFocus, studentId: string): Promise<EvidenceTrace> {
  const check = () => { if (client.evidenceScope !== focus.scope) return unavailable() }
  check()
  const [detail, record] = await Promise.all([client.knowledgeNode(focus.knowledgeId), client.caseRecord(focus.caseId)])
  check()
  const selected = resolveEvidence(focus, studentId, detail, record)
  const read: QuestionRead = await client.questionRevision(focus.homeworkId, focus.questionId!, selected.attempt.captured_question_revision)
  check()
  if (read.record.question_id !== focus.questionId || read.record.homework_id !== focus.homeworkId
    || read.record.revision !== selected.attempt.captured_question_revision || read.current_revision < read.record.revision) return unavailable()
  const [latestDetail, latestRecord] = await Promise.all([client.knowledgeNode(focus.knowledgeId), client.caseRecord(focus.caseId)])
  check()
  const latest = resolveEvidence(focus, studentId, latestDetail, latestRecord)
  if (JSON.stringify(latest.attempt) !== JSON.stringify(selected.attempt) || JSON.stringify(latest.decision) !== JSON.stringify(selected.decision)) return unavailable()
  return { ...latest, question: read.record }
}
