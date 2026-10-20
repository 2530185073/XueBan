import type { CaseDetail, DraftStage, DraftSummary, DraftView } from './core/types'

export type Tone = 'neutral' | 'green' | 'red' | 'amber'
export type StudentResultState = 'correct' | 'incorrect' | 'uncertain' | 'processing' | 'unprocessed' | 'service_failure'

/** Strip only an explicit source label with an unambiguous separator.
 * Bare numbers, decimals, coefficients and exponents are question content.
 */
export function studentQuestionText(stem: string | null, label?: string) {
  const value = stem?.trim() ?? '', source = label?.trim()
  if (!value || !source || !value.startsWith(source)) return value
  const tail = value.slice(source.length)
  const separator = /^(?:[、，:：]\s*|[.．]\s+|[)）]\s+)/.exec(tail)
  // A label which already ends in punctuation (e.g. "(1)") still needs
  // whitespace before the question; "3(1)+2" must remain mathematics.
  const closed = /[、:：)）]$/.test(source) ? /^\s+/.exec(tail) : null
  const remainder = (separator || closed) ? tail.slice((separator || closed)![0].length).trim() : ''
  return remainder || value
}

function currentStudentDecision(row: CaseDetail) {
  const candidate = row.case.current_decision
  return candidate && row.case.lifecycle === 'active' && !['stale', 'retracted'].includes(candidate.validity)
    && candidate.input_versions.question_id === row.case.question_id
    && candidate.input_versions.attempt_id === row.case.current_attempt_id
    && candidate.input_versions.question_revision === row.case.current_question_revision
    && candidate.input_versions.attempt_recognition_revision === row.attempt.recognition_revision ? candidate : null
}

/** A star marks evidence uncertainty without turning a usable verdict into a pending bucket. */
export function studentResultUncertain(row: CaseDetail): boolean {
  const decision = currentStudentDecision(row)
  return !!row.question.recognition_uncertainties?.length
    || !!row.attempt.recognition_uncertainties?.length
    || row.attempt.recognition_status === 'needs_revision' && !!decision
    || decision?.verdict === 'unable_to_judge'
    || decision?.validity === 'needs_review'
    || !!decision?.uncertainties?.length
}

function checkInProgress(row: CaseDetail) {
  return !!row.checkJob && ['queued', 'running', 'retryable_failed'].includes(row.checkJob.status)
}

function unresolvedPresentation(row: CaseDetail): { label: string; tone: Tone; note: string } {
  const reasons = currentStudentDecision(row)?.verification?.reason_codes ?? []
  if (reasons.some(reason => ['provider_unavailable', 'reconciliation_unavailable'].includes(reason))) return {
    label: 'AI 核对暂未完成', tone: 'amber',
    note: '这道题暂不判对错，核对服务没有完成。原图和作答已保留，可以先继续其他题。',
  }
  if (reasons.includes('visual_unreadable')) return {
    label: '这处笔迹暂时看不清，可补拍', tone: 'amber',
    note: '这道题暂不判对错。可以对照原图查看这处笔迹，必要时补拍题目和作答；也可以先继续其他题。',
  }
  return { label: 'AI 还没确认这题', tone: 'amber', note: '这道题暂不判对错。可以对照原图查看，也可以先继续其他题。' }
}

/** A real active check takes priority over the previous result while it is running. */
export function studentResultState(row: CaseDetail): StudentResultState {
  if (checkInProgress(row)) return 'processing'
  const decision = currentStudentDecision(row)
  if (decision && decision.validity !== 'needs_review' && decision.verdict !== 'unable_to_judge') {
    return decision.verdict === 'correct' ? 'correct' : decision.verdict === 'incorrect' || decision.verdict === 'partially_correct' || decision.verdict === 'unanswered' ? 'incorrect' : 'uncertain'
  }
  if (decision) return 'uncertain'
  if (row.checkJob && ['failed', 'superseded', 'cancelled'].includes(row.checkJob.status)) return 'service_failure'
  if (row.attempt.recognition_status === 'pending') return 'processing'
  if (row.attempt.recognition_status === 'failed') return 'service_failure'
  return 'unprocessed'
}

export function casePresentation(row: CaseDetail): { label: string; tone: Tone; note: string } {
  if (checkInProgress(row)) return { label: '正在检查', tone: 'neutral', note: '正在检查这道题的作答，可以先继续其他题。' }
  const decision = row.case.current_decision
  if (!decision) {
    if (row.attempt.recognition_status === 'failed') return { label: '识别服务失败', tone: 'amber', note: '这道题没有生成判断，服务处理没有完成。请稍后重试，或请老师一起看。' }
    if (row.checkJob && ['failed', 'superseded', 'cancelled'].includes(row.checkJob.status)) return { label: '检查服务失败', tone: 'amber', note: '这道题已读到作答，但检查服务没有完成。请稍后重试，或请老师一起看。' }
    if (row.attempt.recognition_status === 'needs_revision') return { label: '识别待核实', tone: 'amber', note: '这道题还没有检查，识别中的不确定之处不会被当成正确。请结合原图核对，必要时请老师一起看。' }
    if (row.attempt.recognition_status !== 'ready') return { label: '正在读题', tone: 'neutral', note: '正在读取题目和你的作答。' }
    return { label: '等待检查', tone: 'neutral', note: '还没有这次作答的判断结果。' }
  }
  const input = decision.input_versions
  if (['stale', 'retracted'].includes(decision.validity) || input.question_id !== row.case.question_id || input.attempt_id !== row.case.current_attempt_id || input.question_revision !== row.case.current_question_revision || input.attempt_recognition_revision !== row.attempt.recognition_revision) return { label: '结果待更新', tone: 'amber', note: '题目或作答已有变化，请刷新后查看。' }
  if (decision.validity === 'needs_review') return unresolvedPresentation(row)
  const confirmed = decision.validity === 'confirmed' && decision.origin === 'staff'
  switch (decision.verdict) {
    case 'correct': return { label: confirmed ? '老师确认正确' : '初判正确', tone: 'green', note: confirmed ? '坐班老师已核实这次作答。' : '这是 AI 的初步判断，仍可请老师核实。' }
    case 'incorrect': return { label: '需要订正', tone: 'red', note: confirmed ? '老师建议再检查一下这次作答。' : 'AI 发现了值得再想一想的地方。先试着自己检查。' }
    case 'partially_correct': return { label: '部分需要订正', tone: 'red', note: '有一部分值得再想一想，看看下面的提示。' }
    case 'unanswered': return { label: '尚未读到作答', tone: 'amber', note: '请确认你已完成作答，并把答案拍清楚。' }
    default: return unresolvedPresentation(row)
  }
}
/** Where the photos of a draft are: only on this device, partly uploaded, or received by the server. */
export function uploadState(d: Pick<DraftSummary, 'stage' | 'homeworkId' | 'pageCount' | 'uploadedCount'>): { label: string; tone: Tone } {
  const pages = d.pageCount > 1 ? `${d.pageCount} 页照片` : '照片'
  if (d.pageCount && !d.uploadedCount) return { label: d.stage === 'local' ? `${pages}只在这台设备，还没上传` : `${pages}还在这台设备，尚未上传完成`, tone: 'amber' }
  if (d.uploadedCount < d.pageCount) return { label: `已上传 ${d.uploadedCount} / ${d.pageCount} 页，其余还在这台设备`, tone: 'amber' }
  if (!d.homeworkId) return { label: '还没有送到服务器', tone: 'amber' }
  if (d.stage === 'complete') return { label: '服务器已收到，可以查看结果', tone: 'green' }
  if (d.stage === 'failed') return { label: '照片已保存在服务器，处理未完成，可重试', tone: 'amber' }
  return { label: '服务器已收到照片，正在处理', tone: 'neutral' }
}
export const stageLabels: Record<DraftStage, string> = { local: '照片已备好', creating: '保存作业', uploading: '上传照片', recognizing: '核对题目与作答', checking: '补充核对作答', complete: '可查看结果', failed: '这次尚未完成', waiting: '进度已保留' }
export function dateLabel(value: string): string { const date = new Date(value); return Number.isNaN(date.getTime()) ? '时间待确认' : new Intl.DateTimeFormat('zh-CN', { month: 'long', day: 'numeric', hour: '2-digit', minute: '2-digit' }).format(date) }

export interface CorrectionTarget { caseId: string; parentAttemptId: string }
export function correctionTarget(row: CaseDetail): CorrectionTarget { return { caseId: row.case.id, parentAttemptId: row.attempt.id } }
export function replacementTarget(draft: DraftView): CorrectionTarget | undefined { return draft.correction ? { caseId: draft.correction.caseId, parentAttemptId: draft.correction.parentAttemptId } : undefined }
export function preferredCase(rows: CaseDetail[], selectedId: string | null, correctedId?: string): CaseDetail | undefined {
  return rows.find(row => row.case.id === selectedId) ?? rows.find(row => row.case.id === correctedId) ?? rows[0]
}
