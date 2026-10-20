import type { Attempt, CaseRecord, Decision, HelpRequest, Hint, Review, Verdict } from './core/types'
import type { Tone } from './presentation'

export type TimelineKind = 'attempt' | 'decision' | 'hint' | 'help' | 'review'
/** current: the case's effective result or answer; historical: kept for the record; invalid: stale/retracted, never current. */
export type TimelineState = 'current' | 'historical' | 'invalid'
export interface TimelineBadge { label: string; tone: Tone }
export interface TimelineEntry { key: string; kind: TimelineKind; at: string; title: string; text?: string; note?: string; badges: TimelineBadge[]; state: TimelineState; assetId?: string }

export const verdictLabels: Record<Verdict, string> = { correct: '正确', incorrect: '需要订正', partially_correct: '部分需要订正', unanswered: '未读到作答', unable_to_judge: '暂时看不准' }
export const helpConditionLabels: Partial<Record<Attempt['help_condition'], string>> = { hint_used: '看过提示', staff_help: '老师帮助', answer_shown: '看过答案' }
export const helpKindLabels: Record<HelpRequest['kind'], string> = { question: '请老师讲题', recognition: '请老师核对识别', judgment: '请老师看判断', appeal: '申请老师重查' }
export const helpStatusLabels: Record<HelpRequest['status'], { label: string; tone: Tone }> = {
  waiting: { label: '等待老师', tone: 'amber' }, claimed: { label: '老师处理中', tone: 'neutral' }, resolved: { label: '老师已处理', tone: 'green' }, unresolved: { label: '老师暂未解决', tone: 'amber' }, cancelled: { label: '已取消', tone: 'neutral' },
}
export const reviewActionLabels: Record<Review['action'], string> = { confirm: '老师确认', replace: '老师改判', retract: '老师撤回判断', leave_unresolved: '老师标记暂不判定', manual_initial: '老师直接判定' }
const hintLevelLabels: Record<Hint['level'], string> = { direction: '方向提示', key_step: '关键一步', staff_handoff: '转给老师' }
const order: Record<TimelineKind, number> = { attempt: 0, decision: 1, hint: 2, help: 3, review: 4 }
const invalidValidity = (validity: string) => validity === 'stale' || validity === 'retracted'
const clip = (text: string | null | undefined, max = 90) => { if (!text) return undefined; const chars = [...text.trim()]; return chars.length > max ? chars.slice(0, max).join('') + '…' : chars.join('') }

/** 原作答 for the root, then 第N次订正 in server creation order. */
export function attemptLabels(attempts: Attempt[]): Map<string, string> {
  const labels = new Map<string, string>(); let n = 0
  for (const a of [...attempts].sort((x, y) => x.created_at.localeCompare(y.created_at) || x.id.localeCompare(y.id))) labels.set(a.id, a.parent_attempt_id === null && a.root_attempt_id === a.id ? '原作答' : `第${++n}次订正`)
  return labels
}
function decisionTitle(d: Decision, reviews: Review[]) {
  if (d.origin !== 'staff') return d.origin === 'rule' ? '规则检查' : 'AI初判'
  const action = reviews.find(r => r.new_decision_id === d.id)?.action
  return action === 'confirm' ? '老师确认' : action === 'replace' ? '老师改判' : '老师判断'
}

/**
 * Chronological record of one case. "Current" comes only from the case's own
 * current_decision/current_attempt together with the server's
 * currently_effective flag, so a refresh can never revive a stale result.
 */
export function buildTimeline(record: CaseRecord): TimelineEntry[] {
  const c = record.case, active = c.lifecycle === 'active', labels = attemptLabels(record.attempts.map(a => a.record)), entries: TimelineEntry[] = []
  for (const { record: a, currently_effective } of record.attempts) {
    const current = active && currently_effective && a.id === c.current_attempt_id, badges: TimelineBadge[] = []
    const help = helpConditionLabels[a.help_condition]; if (help) badges.push({ label: help, tone: a.help_condition === 'answer_shown' ? 'amber' : 'neutral' })
    if (a.recognition_status !== 'ready') badges.push({ label: a.recognition_status === 'pending' ? '正在读题' : '识别待核实', tone: 'amber' })
    if (current) badges.push({ label: '当前作答', tone: 'green' })
    entries.push({ key: `attempt:${a.id}`, kind: 'attempt', at: a.created_at, title: labels.get(a.id) ?? '作答', text: clip(a.answer_text) ?? '暂未读到清楚的作答', badges, state: current ? 'current' : 'historical', assetId: a.asset_ids[0] })
  }
  const effective = c.current_decision && !invalidValidity(c.current_decision.validity) && active ? c.current_decision : null
  for (const { record: d, currently_effective } of record.decisions) {
    const invalid = invalidValidity(d.validity), current = !invalid && currently_effective && effective?.id === d.id && effective.revision === d.revision
    const badges: TimelineBadge[] = []
    if (invalid) badges.push({ label: '已失效', tone: 'neutral' })
    else if (current) badges.push({ label: '当前结果', tone: d.verdict === 'correct' ? 'green' : d.verdict === 'incorrect' || d.verdict === 'partially_correct' ? 'red' : 'amber' })
    else badges.push({ label: '历史结果', tone: 'neutral' })
    if (!invalid && d.validity === 'needs_review') badges.push({ label: '尚待核实', tone: 'amber' })
    const target = labels.get(d.input_versions.attempt_id)
    entries.push({ key: `decision:${d.id}`, kind: 'decision', at: d.confirmed_at ?? d.created_at, title: `${decisionTitle(d, record.reviews)} · ${verdictLabels[d.verdict]}`, note: target ? `针对${target}` : undefined, badges, state: invalid ? 'invalid' : current ? 'current' : 'historical' })
  }
  for (const h of record.hints) {
    if (!h.first_shown_at) continue
    const invalid = invalidValidity(h.validity)
    entries.push({ key: `hint:${h.id}`, kind: 'hint', at: h.first_shown_at, title: `看过提示 · ${hintLevelLabels[h.level]}`, note: labels.get(h.attempt_id) ? `针对${labels.get(h.attempt_id)}` : undefined, badges: [h.exposes_answer ? { label: '提示含答案', tone: 'amber' } : { label: '未给出答案', tone: 'neutral' }, ...(invalid ? [{ label: '已失效', tone: 'neutral' as Tone }] : [])], state: invalid ? 'invalid' : 'historical' })
  }
  for (const h of record.help) {
    const status = helpStatusLabels[h.status]
    entries.push({ key: `help:${h.id}`, kind: 'help', at: h.created_at, title: helpKindLabels[h.kind], text: clip(h.note, 140), note: h.resolution_note ? `老师说明：${clip(h.resolution_note, 300)}` : undefined, badges: [status], state: 'historical' })
  }
  for (const r of record.reviews) entries.push({ key: `review:${r.id}`, kind: 'review', at: r.created_at, title: reviewActionLabels[r.action], text: clip(r.reason, 300), badges: [], state: 'historical' })
  const time = (value: string) => { const t = Date.parse(value); return Number.isFinite(t) ? t : 0 }
  return entries.sort((a, b) => time(a.at) - time(b.at) || order[a.kind] - order[b.kind] || a.key.localeCompare(b.key))
}

/** Help requests on the current answer, newest first, for the status card above the timeline. */
export function currentHelp(record: CaseRecord): HelpRequest[] {
  return record.help.filter(h => h.attempt_id === record.case.current_attempt_id || h.status === 'waiting' || h.status === 'claimed').sort((a, b) => b.created_at.localeCompare(a.created_at))
}
