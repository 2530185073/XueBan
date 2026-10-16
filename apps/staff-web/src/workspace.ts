import type {
  AttemptRead, CheckRun, CorrectionCase, DecisionRead, HelpRequest, Hint, HomeworkSession, ImageAsset, QuestionRead, Region, RepairLayout,
  Review, SubmitReview, Verdict,
} from './api/contracts.generated'
import type { StaffApi } from './api/client'

export interface CaseWorkspace {
  correction: CorrectionCase
  homework: HomeworkSession
  question: QuestionRead
  attempts: AttemptRead[]
  hints: Hint[]
  decision: DecisionRead | null
  check: CheckRun | null
  helps: HelpRequest[]
  reviews: Review[]
  assets: ImageAsset[]
}

export async function loadWorkspace(api: StaffApi, caseId: string, signal?: AbortSignal): Promise<CaseWorkspace> {
  const correction = await api.case(caseId, signal)
  const decisionId = correction.current_decision?.id
  const [homework, question, attempts, hints, decision, check, helps, reviews] = await Promise.all([
    api.homework(correction.homework_id, signal),
    // The revision the case refers to; superseded cases keep showing the question they were about.
    api.question(correction.question_id, correction.current_question_revision, signal),
    Promise.all(correction.attempt_ids.map(id => api.attempt(id, signal))),
    Promise.all(correction.hint_ids.map(id => api.hint(id, signal))),
    decisionId ? api.decision(decisionId, signal) : Promise.resolve(null),
    correction.current_decision?.check_id ? api.check(correction.current_decision.check_id, signal) : Promise.resolve(null),
    Promise.all(correction.help_request_ids.map(id => api.help(id, signal))),
    Promise.all(correction.review_ids.map(id => api.review(id, signal))),
  ])
  const assets = await Promise.all(homework.asset_ids.map(id => api.imageAsset(id, signal)))
  return { correction, homework, question, attempts, hints, decision, check, helps, reviews, assets: assets.filter(asset => asset.status === 'uploaded').sort((a, b) => a.page_index - b.page_index) }
}

/** Active questions of the homework, for choosing which ones a layout repair supersedes. */
export async function loadHomeworkQuestions(api: StaffApi, homework: HomeworkSession, signal?: AbortSignal): Promise<QuestionRead[]> {
  const questions = await Promise.all(homework.question_ids.map(id => api.question(id, undefined, signal)))
  return questions.filter(question => question.currently_effective && question.record.lifecycle === 'active')
}

export interface ChainEntry { attempt: AttemptRead; label: string; parentLabel: string | null; current: boolean; depth: number }

/** Root first, then each correction after the attempt it corrects; unreachable attempts are listed last. */
export function attemptChain(attempts: AttemptRead[], currentId: string): ChainEntry[] {
  const byTime = (a: AttemptRead, b: AttemptRead) => Date.parse(a.record.occurred_at) - Date.parse(b.record.occurred_at)
  const children = new Map<string | null, AttemptRead[]>()
  const ids = new Set(attempts.map(a => a.record.id))
  for (const attempt of attempts) {
    const parent = attempt.record.parent_attempt_id && ids.has(attempt.record.parent_attempt_id) ? attempt.record.parent_attempt_id : attempt.record.parent_attempt_id ? 'orphan' : null
    children.set(parent, [...(children.get(parent) ?? []), attempt])
  }
  const labels = new Map<string, string>()
  const result: ChainEntry[] = []
  let corrections = 0
  const visit = (attempt: AttemptRead, depth: number) => {
    if (labels.has(attempt.record.id)) return
    const label = attempt.record.parent_attempt_id === null ? '原始作答' : `第 ${++corrections} 次订正`
    labels.set(attempt.record.id, label)
    result.push({ attempt, label, parentLabel: attempt.record.parent_attempt_id ? labels.get(attempt.record.parent_attempt_id) ?? '上一版本' : null, current: attempt.record.id === currentId, depth })
    for (const child of (children.get(attempt.record.id) ?? []).sort(byTime)) visit(child, depth + 1)
  }
  for (const root of (children.get(null) ?? []).sort(byTime)) visit(root, 0)
  for (const orphan of (children.get('orphan') ?? []).sort(byTime)) visit(orphan, 1)
  return result
}

export function currentAttempt(ws: CaseWorkspace): AttemptRead | undefined {
  return ws.attempts.find(attempt => attempt.record.id === ws.correction.current_attempt_id)
}

/** The help request in focus: the one opened from the queue, else the newest one still waiting or claimed. */
export function focusHelp(ws: CaseWorkspace, helpId: string | null): HelpRequest | null {
  if (helpId) { const chosen = ws.helps.find(help => help.id === helpId); if (chosen) return chosen }
  return [...ws.helps].filter(help => help.status === 'waiting' || help.status === 'claimed').sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at))[0] ?? null
}

/** Why the teacher cannot change this case right now, or null. */
export function editBlock(ws: CaseWorkspace, focus: HelpRequest | null, actorId: string | undefined, sample: boolean): string | null {
  if (sample) return '合成预览不会提交或保存任何操作。'
  if (ws.homework.processing_blocked) return '这份作业已停止处理，不能继续操作。'
  if (ws.correction.lifecycle !== 'active') return '此题已被补题/拆合替代，不能再修改；请打开替代后的新题。'
  if (focus?.status === 'waiting') return '孩子正在求助，请先接手这条求助，再提交处理记录。'
  if (focus?.status === 'claimed' && focus.claimed_by !== actorId) return '这条求助已由其他老师接手。'
  return null
}

/** Why a review cannot be submitted against the loaded versions, or null. */
export function reviewBlock(ws: CaseWorkspace): string | null {
  const attempt = currentAttempt(ws)
  if (!attempt?.currently_effective) return '找不到当前有效的作答，请刷新后核实。'
  if (!ws.question.currently_effective || ws.question.record.lifecycle !== 'active') return '题目版本已变化，请刷新后核实。'
  if (attempt.record.recognition_status === 'pending' || attempt.record.recognition_status === 'failed') return '这次作答的识别尚未完成，请先修订答案。'
  const decision = ws.decision
  if (decision) {
    const versions = decision.record.input_versions
    if (!decision.currently_effective || ['stale', 'retracted'].includes(decision.record.validity) || versions.question_revision !== ws.question.current_revision ||
      versions.attempt_id !== attempt.record.id || versions.attempt_recognition_revision !== attempt.record.recognition_revision) return '当前判断依据的版本已变化，请刷新后核实。'
  }
  return null
}

export interface ReviewForm { action: SubmitReview['action']; verdict: Verdict | ''; reason: string; basis: string; regions: Region[] }

export function reviewVerdict(ws: CaseWorkspace, form: ReviewForm): Verdict | null {
  if (form.action === 'confirm') return ws.decision?.record.verdict ?? null
  if (form.action === 'retract' || form.action === 'leave_unresolved') return null
  return form.verdict || null
}

export function reviewIssue(ws: CaseWorkspace, form: ReviewForm): string | null {
  if (form.action !== 'manual_initial' && !ws.decision) return '当前没有可复核的判断，只能人工初次核实。'
  if (form.action === 'manual_initial' && ws.decision) return '已有判断，请确认、更正、保留或撤销它。'
  const verdict = reviewVerdict(ws, form)
  if (['confirm', 'replace', 'manual_initial'].includes(form.action) && (!verdict || verdict === 'unable_to_judge')) {
    return form.action === 'confirm' ? 'AI 无法判断的候选不能直接确认，请选择“更正当前判断”。' : '请选择核实结果。'
  }
  if (!form.reason.trim()) return '请写下处理说明。'
  if (!form.basis.trim()) return '请写下在原图中核实的依据。'
  return null
}

export function buildReview(ws: CaseWorkspace, form: ReviewForm): SubmitReview {
  const attempt = currentAttempt(ws)!
  const target = form.action === 'manual_initial' ? null : ws.decision!.record
  return {
    expected_revision: ws.correction.revision,
    target_decision_id: target?.id ?? null, target_decision_revision: target?.revision ?? null,
    action: form.action, verdict: reviewVerdict(ws, form), reason: form.reason.trim(),
    basis: [{ kind: 'staff_observation', summary: form.basis.trim(), source_ref: null, regions: form.regions }],
    attempt_id: attempt.record.id, question_revision: ws.question.current_revision,
    attempt_recognition_revision: attempt.record.recognition_revision,
  }
}

export interface ReplacementDraft { regions: Region[]; stem: string }
export const MAX_REPLACEMENTS = 12

export function repairIssue(groups: ReplacementDraft[], reason: string): string | null {
  if (groups.length === 0) return '请至少框出一道新题。'
  if (groups.length > MAX_REPLACEMENTS) return `一次最多补充或拆分 ${MAX_REPLACEMENTS} 道题。`
  if (groups.some(group => group.regions.length === 0)) return '每道新题至少需要框出一个区域。'
  if (!reason.trim()) return '请写下补题或拆合的原因。'
  return null
}

export function buildRepair(homework: HomeworkSession, supersede: QuestionRead[], groups: ReplacementDraft[], reason: string): RepairLayout {
  return {
    expected_revision: homework.revision,
    supersede_questions: supersede.map(question => ({ question_id: question.record.question_id, expected_revision: question.current_revision })),
    replacement_regions: groups.map(group => ({ regions: group.regions, stem_text: group.stem.trim() || null })),
    reason: reason.trim(),
  }
}

/** Case ids that a finished layout repair created, compared with the homework before it. */
export function newCaseIds(before: string[], after: HomeworkSession): string[] {
  const known = new Set(before)
  return after.case_ids.filter(id => !known.has(id))
}
