import type { AttemptRead, Hint } from './api/contracts.generated'
import type {
  AgentRun, AssistanceInput, Certainty, Delivery, Evidence, GateReason, KnowledgeLink, KnowledgeNode, LearningStatus, LinkRole, Memory, MemoryInput, MemoryStatus,
  Report, StaffMemoryCategory,
} from './api/learningTypes'

// Mastery is only ever shown as these words, never as a percentage or score.
export const statusLabels: Record<LearningStatus, string> = {
  needs_help_now: '当前需要帮助', consolidating: '正在巩固', stable_for_now: '阶段性较稳', no_judgment: '暂不判断',
}
export const statusTone: Record<LearningStatus, string> = { needs_help_now: 'rose', consolidating: 'amber', stable_for_now: '', no_judgment: 'muted' }
export const statusLabel = (status: string) => statusLabels[status as LearningStatus] ?? '暂不判断'

export const reasonLabels: Record<string, string> = {
  single_error_needs_diagnosis: '一次经核实的错误，需要先诊断原因', repeated_errors: '多次经核实的错误',
  independent_success_awaiting_more_dates: '已独立做对，等待不同日期再次确认', corrected_with_help_awaiting_new_school_item: '在帮助下订正正确，等待新的学校题目',
  assisted_or_unverified_only: '只有帮助下或未核实独立性的作答', no_scorable_evidence: '暂无可评分的已核实作答',
}
export const openQuestionLabels: Record<string, string> = { independence_unverified: '独立完成情况未核实', cause_not_diagnosed: '错误原因尚未诊断' }
export const sufficiencyLabels: Record<string, string> = { substantial: '证据较充分', limited: '证据有限', insufficient: '证据不足' }

export const assistanceLabels: Record<string, string> = {
  independent: '独立完成（老师核实）', no_recorded_help: '未记录帮助', direction: '看过提示方向', key_step: '看过关键步骤提示',
  staff_help: '老师现场帮助', solution_seen: '看过答案或解法', unknown: '帮助情况未知',
}
export const assistanceChoices: AssistanceInput[] = ['independent', 'direction', 'key_step', 'staff_help', 'solution_seen', 'unknown']
export const resultLabels: Record<string, string> = { correct: '答对', incorrect: '答错', partial: '部分正确', unscorable: '不计对错' }
export const eventLabels: Record<string, string> = { school_first_attempt: '学校作业首次作答', correction: '订正' }
export const invalidationLabels: Record<string, string> = { superseded: '已被新的记录取代', withdrawn: '依据已撤回（改判、修订或关联被驳回）' }
export const verdictLabels: Record<string, string> = { correct: '正确', incorrect: '需要订正', partially_correct: '部分正确', unanswered: '未作答', unable_to_judge: '无法判断' }
export const roleLabels: Record<LinkRole, string> = { primary: '主要（定位错误）', secondary: '次要' }

export const memoryCategoryLabels: Record<string, string> = {
  effective_support: '有效的帮助方式', support_agreement: '帮助约定', learning_goal: '学习目标', recurring_difficulty: '反复出现的学科困难',
  progress_milestone: '进步记录', interface_preference: '界面偏好',
}
export const staffMemoryCategories: StaffMemoryCategory[] = ['effective_support', 'support_agreement', 'learning_goal', 'recurring_difficulty']
export const memorySourceLabels: Record<Memory['source'], string> = { system_rule: '系统规则（依据已核实作答）', model: 'AI 提议', staff: '老师', student: '孩子本人' }
export const memoryStatusLabels: Record<MemoryStatus, string> = {
  proposed: '待老师确认', confirmed: '有效', expired: '已过期', superseded: '已被新记忆取代', invalidated: '已失效', retracted: '已撤回',
}
export const memoryReasonLabels: Record<string, string> = {
  evidence_withdrawn: '引用的证据已被撤销', newer_evidence: '有更新的证据', replaced: '已被替换', retracted_by_staff: '老师撤回', retracted_by_student: '孩子撤回',
}
export const MEMORY_MAX = 120

export const certaintyLabels: Record<Certainty, string> = { low: '把握较低', medium: '把握一般', high: '把握较高' }

export type RunView = 'queued' | 'ready' | 'needs_evidence' | 'needs_human' | 'stale' | 'failed'
export const runViewLabels: Record<RunView, string> = {
  queued: '正在生成…', ready: '已生成 · 仅供老师参考', needs_evidence: '证据不足，暂不能形成摘要', needs_human: '需要老师判断',
  stale: '已过期：引用的证据已被撤销，请重新生成', failed: '未能生成',
}
/** A completed run reports its own outcome; every other run status wins over the output. */
export function runView(run: AgentRun): RunView {
  if (run.status === 'completed') return run.output?.status === 'needs_evidence' ? 'needs_evidence' : run.output?.status === 'needs_human' ? 'needs_human' : 'ready'
  return run.status
}
export const failureLabels: Record<string, string> = { provider_disabled: '模型服务未开启' }

export const reportStatusLabels: Record<Report['status'], string> = { draft: '草稿', published: '已发布', superseded: '已被更正版本取代' }
export const regeneratedLabels: Record<string, string> = {
  scheduled: '定时生成', requested: '老师手动生成', facts_changed: '事实变化后自动更新', correction_required: '已发布内容的事实有变，需发布更正版本',
}
export const gateReasonLabels: Record<GateReason, string> = {
  school_tasks_not_confirmed: '学校任务尚未确认完成', capacity_not_observed: '老师还没确认孩子今天有余力', no_extra_budget: '没有开放额外时间（或已用完）',
}
export const sessionStatusLabels: Record<string, string> = { active: '进行中', completed: '已完成', exited: '孩子选择结束', time_up: '时间到' }
export const exitReasonLabels: Record<string, string> = { tired: '累了', not_now: '现在不想做', done: '做完了', other: '其他' }
export const itemStatusLabels: Record<string, string> = { candidate: '待审核', approved: '已通过', rejected: '已驳回', retired: '已停用' }
export const answerKindLabels: Record<string, string> = { number: '数值', choice: '选择题', text: '文字' }

// Asia/Shanghai has no daylight saving time, so a fixed +8 h offset is exact.
const SHANGHAI_MS = 8 * 3600_000
const pad = (n: number) => String(n).padStart(2, '0')
const iso = (year: number, month: number, dayOfMonth: number) => `${year}-${pad(month)}-${pad(dayOfMonth)}`

export function shanghaiDate(at: number): { year: number; month: number; day: number } {
  const local = new Date(at + SHANGHAI_MS)
  return { year: local.getUTCFullYear(), month: local.getUTCMonth() + 1, day: local.getUTCDate() }
}

/** Left-closed, right-open half-month containing a calendar day: [1, 16) or [16, first of next month). */
export function periodOf(year: number, month: number, dayOfMonth: number): { start: string; endExclusive: string } {
  if (dayOfMonth < 16) return { start: iso(year, month, 1), endExclusive: iso(year, month, 16) }
  const next = month === 12 ? [year + 1, 1] : [year, month + 1]
  return { start: iso(year, month, 16), endExclusive: iso(next[0], next[1], 1) }
}

export function periodFromStart(start: string): { start: string; endExclusive: string } | null {
  const match = /^(\d{4})-(\d{2})-(01|16)$/.exec(start)
  if (!match) return null
  const [year, month] = [Number(match[1]), Number(match[2])]
  if (month < 1 || month > 12) return null
  return periodOf(year, month, Number(match[3]))
}

/** The most recent finished half-months in Asia/Shanghai, newest first; the current one is never included. */
export function finishedPeriods(at: number, howMany = 6): string[] {
  const today = shanghaiDate(at)
  let { year, month } = today
  let half: 1 | 16 = today.day < 16 ? 1 : 16
  const output: string[] = []
  while (output.length < howMany) {
    if (half === 16) half = 1
    else { half = 16; month -= 1; if (month === 0) { month = 12; year -= 1 } }
    output.push(iso(year, month, half))
  }
  return output
}
export const lastFinishedPeriod = (at: number) => finishedPeriods(at, 1)[0]

/** Shanghai midnight at the end of the window has passed. */
export function periodFinished(start: string, at: number): boolean {
  const period = periodFromStart(start)
  if (!period) return false
  const [year, month, dayOfMonth] = period.endExclusive.split('-').map(Number)
  return Date.UTC(year, month - 1, dayOfMonth) - SHANGHAI_MS <= at
}

export function periodLabel(start: string, endExclusive?: string): string {
  const period = periodFromStart(start)
  const end = endExclusive ?? period?.endExclusive
  if (!end) return start
  const [year, month, dayOfMonth] = end.split('-').map(Number)
  const last = new Date(Date.UTC(year, month - 1, dayOfMonth) - 86_400_000)
  return `${start} 至 ${iso(last.getUTCFullYear(), last.getUTCMonth() + 1, last.getUTCDate())}`
}

const SHARE_PATH = /^\/r\/[A-Za-z0-9_-]{20,128}$/
/** Guardian link on the service origin; anything but a bare /r/{token} path is refused. */
export function shareUrl(origin: string, path: string): string {
  if (!SHARE_PATH.test(path)) throw new Error('Unexpected share path')
  const base = new URL(origin)
  if (!['http:', 'https:'].includes(base.protocol)) throw new Error('Unexpected origin')
  return `${base.origin}${path}`
}

export type ShareState = 'active' | 'revoked' | 'expired'
export function shareState(link: { revoked_at: string | null; expires_at: string }, at: number): ShareState {
  if (link.revoked_at) return 'revoked'
  return Date.parse(link.expires_at) <= at ? 'expired' : 'active'
}

/** The system already saw a hint or answer for this attempt, so it cannot be marked independent. */
export function helpSeen(attempt: AttemptRead, hints: Hint[]): boolean {
  return ['hint_used', 'answer_shown'].includes(attempt.record.help_condition) || hints.some(hint => hint.attempt_id === attempt.record.id && !!hint.first_shown_at)
}

export const selectableNodes = (nodes: KnowledgeNode[]) => nodes.filter(node => node.node_type === 'concept' || node.node_type === 'skill')

export function searchNodes(nodes: KnowledgeNode[], term: string): KnowledgeNode[] {
  const query = term.trim().toLowerCase()
  return query ? nodes.filter(node => node.name.toLowerCase().includes(query) || node.id.includes(query) || (node.definition ?? '').includes(query)) : nodes
}

/** At most one confirmed primary link per question. */
export function primaryConflict(links: KnowledgeLink[], knowledgeId: string, role: LinkRole, replacing: string | null = null): string | null {
  if (role !== 'primary') return null
  const other = links.find(link => link.status === 'confirmed' && link.role === 'primary' && link.knowledge_id !== knowledgeId && link.knowledge_id !== replacing)
  return other ? `已有主要知识点「${other.knowledge_name ?? other.knowledge_id}」。一道题只能有一个主要知识点，请先把它改为次要或驳回。` : null
}

export function memoryIssue(input: MemoryInput, validEvidence: Set<string>): string | null {
  const content = input.content.trim()
  if (content.length < 2) return '请写下记忆内容（至少 2 个字）。'
  if (input.content.length > MEMORY_MAX) return `记忆内容最多 ${MEMORY_MAX} 个字。`
  if (input.evidence_ids.length > 20) return '最多引用 20 条证据。'
  if (input.evidence_ids.some(id => !validEvidence.has(id))) return '只能引用该学生当前有效的证据，请刷新后重新选择。'
  if (input.category === 'recurring_difficulty' && input.evidence_ids.length === 0) return '学科困难必须引用至少一条已核实的作答证据。'
  return null
}

export function diagnosisIssue(kind: 'suspected_cause' | 'verified_weak_scope', scope: string, evidenceIds: string[]): string | null {
  if (scope.trim().length < 2) return '请写明具体范围或原因（至少 2 个字）。'
  if (scope.length > 500) return '最多 500 个字。'
  if (kind === 'verified_weak_scope' && evidenceIds.length < 2) return '已核实薄弱范围至少需要两条相关证据。'
  return null
}

export function parseAnswerValues(raw: string): string[] {
  return raw.split(/[\n,，、]/).map(value => value.trim()).filter(Boolean)
}

export function itemIssue(input: { knowledge_id: string; stem_text: string; answer_values: string[]; solution_text: string; hint_text: string | null }): string | null {
  if (!input.knowledge_id) return '请选择知识点。'
  if (input.stem_text.trim().length < 2 || input.stem_text.length > 600) return '题干需 2–600 个字。'
  if (input.answer_values.length < 1 || input.answer_values.length > 4) return '请填写 1–4 个可接受的答案（用逗号或换行分隔）。'
  if (input.answer_values.some(value => value.length > 60)) return '每个答案最多 60 个字。'
  if (!input.solution_text.trim() || input.solution_text.length > 800) return '请填写解题步骤（最多 800 个字）。'
  if ((input.hint_text ?? '').length > 200) return '提示最多 200 个字。'
  return null
}

/** Valid evidence first, newest first. */
export function orderEvidence(items: Evidence[]): Evidence[] {
  return [...items].sort((a, b) => Number(a.validity !== 'valid') - Number(b.validity !== 'valid') || Date.parse(b.occurred_at) - Date.parse(a.occurred_at))
}

export const shortDate = (value: string) => new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit' }).format(new Date(value))
/** Today and the days before it in Asia/Shanghai, newest first. */
export function recentDays(at: number, howMany = 7): string[] {
  return Array.from({ length: howMany }, (_, i) => {
    const { year, month, day } = shanghaiDate(at - i * 86_400_000)
    return iso(year, month, day)
  })
}

export function dayLabel(value: string, at: number): string {
  const [today, yesterday] = recentDays(at, 2)
  const [, month, dayOfMonth] = value.split('-').map(Number)
  return `${month}月${dayOfMonth}日${value === today ? '（今天）' : value === yesterday ? '（昨天）' : ''}`
}

/** The text a teacher pastes into the guardian chat; the link is the only personal data in it besides the name. */
/** Row label for a guardian feedback: publishing, copying and a teacher's send confirmation are separate facts;
 * none of them proves delivery, only a link being opened shows the guardian read it. */
export function deliveryLabel(report: { status: string; supersedes_id: string | null; delivery: Delivery | null } | null): [string, string] {
  if (!report) return ['今天没有记录', 'muted']
  if (report.status === 'draft') return report.supersedes_id ? ['有更新待发布', 'rose'] : ['待核对发布', 'amber']
  const delivery = report.delivery
  if (delivery?.opened_at) return ['家长已打开', '']
  if (delivery?.sent_confirmed_at) return ['老师已确认发送', '']
  if (delivery?.copied) return ['已复制，待确认发送', 'amber']
  if (delivery && delivery.links > 0) return ['已生成链接，未复制', 'amber']
  return ['已发布，未发给家长', 'amber']
}

export function dailyMessage(name: string, value: string, url: string): string {
  const [, month, dayOfMonth] = value.split('-').map(Number)
  return `【学伴】${name} ${month}月${dayOfMonth}日的学习反馈已整理好（今天交的作业、错题订正情况和老师的话），请点开查看：${url}`
}

export const dateTime = (value: string) => new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }).format(new Date(value))
