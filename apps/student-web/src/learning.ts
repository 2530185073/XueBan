import { MAX_PRACTICE_ITEMS, type AgentContext, type AgentTip, type ChapterRollup, type EvidenceResult, type ExitReason, type KnowledgeMap, type KnowledgeState, type LearningStatus, type Memory, type OptionalActivities, type PracticeResult, type PracticeSession, type PracticeStatus, type PreviewOffer, type VariantOffer } from './core/learning'
import { StudentError } from './core/errors'
import type { Tone } from './presentation'

/** Plain-word learning status. Never a score, percentage or rank. */
export const statusLabels: Record<LearningStatus, { label: string; tone: Tone }> = {
  needs_help_now: { label: '需要再看看', tone: 'amber' },
  consolidating: { label: '正在巩固', tone: 'neutral' },
  stable_for_now: { label: '阶段性较稳', tone: 'green' },
  no_judgment: { label: '还不判断', tone: 'neutral' },
}
export const statusOrder: LearningStatus[] = ['needs_help_now', 'consolidating', 'stable_for_now', 'no_judgment']
const reasonTexts: Record<string, string> = {
  independent_success_awaiting_more_dates: '你已经自己做对过，过几天在新题上也做对，就更稳了。',
  corrected_with_help_awaiting_new_school_item: '在帮助下订正对了，等下次学校的新题再看看。',
  repeated_errors: '最近几道题在这里卡住过，可以和老师一起找找是哪一步。',
  single_error_needs_diagnosis: '有一道题在这里没做对，原因还要和老师一起看看。',
  assisted_or_unverified_only: '目前的作答带过提示或还没核实，先不下判断。',
  no_scorable_evidence: '还没有能用来判断的作答，先不下判断。',
}
const statusReasons: Record<LearningStatus, string> = {
  stable_for_now: '在不同的日子里，自己做对了不同的新题。',
  consolidating: '正在一点点变熟练。',
  needs_help_now: '最近在这里遇到了困难，可以请老师一起看看。',
  no_judgment: '资料还不够，先不下判断。',
}
export function reasonText(status: LearningStatus, codes: string[]): string {
  const known = codes.map(code => reasonTexts[code]).filter(Boolean)
  return known.length ? known.join('') : statusReasons[status]
}

export const resultLabels: Record<EvidenceResult, string> = { correct: '做对了', incorrect: '当时需要订正', partial: '部分需要订正', unscorable: '没法判断' }
const assistanceLabels: Record<string, string> = {
  independent: '自己独立完成', no_recorded_help: '没有记录到帮助', direction: '看过方向提示', key_step: '看过关键一步',
  solution_seen: '看过解答', staff_help: '老师帮助过', unknown: '帮助情况没有记录',
}
export const assistanceLabel = (level: string) => assistanceLabels[level] ?? '帮助情况没有记录'
export const eventLabel = (type: string) => type === 'correction' ? '订正' : '第一次作答'
export function dayLabel(value: string, now = new Date()): string { const d = new Date(value); return Number.isNaN(d.getTime()) ? '日期待确认' : new Intl.DateTimeFormat('zh-CN', { year: d.getFullYear() === now.getFullYear() ? undefined : 'numeric', month: 'long', day: 'numeric' }).format(d) }

/** The map entry appears only when the server has at least one knowledge state. */
export const hasKnowledgeMap = (map: KnowledgeMap | null | undefined): map is KnowledgeMap => !!map && map.states.length > 0
export const PENDING_NOTICE = '老师刚更新了批改，地图稍后刷新'
export function chapterCounts(chapter: ChapterRollup): { status: LearningStatus; label: string; count: number }[] {
  return statusOrder.filter(s => (chapter.status_counts[s] ?? 0) > 0).map(s => ({ status: s, label: statusLabels[s].label, count: chapter.status_counts[s]! }))
}
export interface ChapterGroup { id: string; name: string; states: KnowledgeState[] }
export function groupStates(states: KnowledgeState[]): ChapterGroup[] {
  const groups = new Map<string, ChapterGroup>()
  for (const state of states) {
    const key = state.chapter_id ?? '', group = groups.get(key) ?? { id: key, name: state.chapter_name || '其他知识点', states: [] }
    group.states.push(state); groups.set(key, group)
  }
  return [...groups.values()]
}

export const MAX_TIPS = 3
const tipPrefix = /^学伴记得[:：]\s*/
/** Up to three tips with the redundant "学伴记得：" lead-in removed; empty means hide the panel. */
export function visibleTips(context: AgentContext | null | undefined): (AgentTip & { display: string })[] {
  return (context?.tips ?? []).filter(tip => tip.text.trim()).slice(0, MAX_TIPS).map(tip => ({ ...tip, display: tip.text.replace(tipPrefix, '') }))
}

export const memoryCategoryLabels: Record<string, string> = { interface_preference: '我的偏好', learning_goal: '我的小目标', support_agreement: '和老师的约定', effective_support: '对我有用的帮助' }
const shownCategories = new Set(Object.keys(memoryCategoryLabels))
/** Preferences, goals and agreements only; dated evidence summaries surface in 学伴记得 next to a question instead. */
export function profileMemories(items: Memory[], actorId: string | null | undefined) {
  return items.filter(m => m.status === 'confirmed' && shownCategories.has(m.category)).map(m => ({ ...m, own: m.source === 'student' && !!actorId && m.confirmed_by === actorId }))
}

export interface ActivityOffers { remainingMinutes: number; variants: VariantOffer[]; preview: PreviewOffer | null; notice: string }
/** Nothing is offered unless the server says the gate is open and there is something to choose. */
export function activityOffers(activities: OptionalActivities | null | undefined): ActivityOffers | null {
  if (!activities?.gate.open || activities.gate.remaining_minutes <= 0) return null
  const variants = activities.variants.filter(v => v.available_items > 0 && (v.learning_status === 'needs_help_now' || v.learning_status === 'consolidating'))
  const preview = activities.preview && activities.preview.knowledge_ids.length ? activities.preview : null
  if (!variants.length && !preview) return null
  return { remainingMinutes: activities.gate.remaining_minutes, variants, preview, notice: activities.notice }
}
export const variantMax = (offer: VariantOffer) => Math.max(1, Math.min(MAX_PRACTICE_ITEMS, offer.available_items))
export const variantDefault = (offer: VariantOffer) => Math.min(2, variantMax(offer))
export const clampCount = (value: number, offer: VariantOffer) => Math.max(1, Math.min(variantMax(offer), Math.round(Number.isFinite(value) ? value : variantDefault(offer))))

/** Remaining activity time from the server's budget end, in calm words. */
export function remainingLabel(budgetEndsAt: string, now = Date.now()): string {
  const ms = Date.parse(budgetEndsAt) - now
  if (!Number.isFinite(ms) || ms <= 0) return '时间到了'
  if (ms < 60_000) return '不到 1 分钟'
  return `约 ${Math.round(ms / 60_000)} 分钟`
}
/** The server marks a session time_up only on its next write, so a past budget end already counts as ended here. */
export function effectiveStatus(session: Pick<PracticeSession, 'status' | 'budget_ends_at'>, now = Date.now()): PracticeStatus {
  return session.status === 'active' && Date.parse(session.budget_ends_at) <= now ? 'time_up' : session.status
}
export const wrapUps: Record<Exclude<PracticeStatus, 'active'>, { title: string; text: string }> = {
  completed: { title: '这几题都做完啦', text: '愿意多试一试，已经很棒了。现在可以休息，或者结束今天的学习。' },
  time_up: { title: '今天的时间用完啦', text: '没做完的题不用补，也不会记欠账。休息一下吧。' },
  exited: { title: '已经结束啦', text: '随时结束都可以，不会记欠账，也不影响知识地图。' },
}
export const exitChoices: { reason: ExitReason; label: string }[] = [
  { reason: 'tired', label: '有点累了' }, { reason: 'not_now', label: '现在不想做' }, { reason: 'done', label: '做得差不多了' }, { reason: 'other', label: '就是想结束' },
]
export const answerFeedback: Record<PracticeResult, { title: string; text: string; tone: Tone }> = {
  correct: { title: '做对了', text: '可以看看解析，确认自己的思路。', tone: 'green' },
  incorrect: { title: '这次没对上，没关系', text: '对照解析看看哪一步不一样，想明白就是收获。', tone: 'amber' },
  unscorable: { title: '没法自动判断，可以对照解析', text: '学伴读不准这个写法，自己对照一下就好。', tone: 'neutral' },
}
export const choiceLetters = ['A', 'B', 'C', 'D'] as const

const startMessages: Record<string, string> = {
  optional_gate_closed: '现在先不开始额外活动。等学校任务完成、老师开放时间后才可以选。今天就到这里也很好。',
  practice_not_needed: '这个知识点现在不需要额外练习，休息一下也很好。',
  course_unknown: '老师还没告诉学伴下一课学什么，预习先不开放。',
  no_reviewed_items: '这个知识点还没有老师看过的练习题，先休息一下吧。',
  revision_conflict: '你还有一个没结束的活动，可以接着做，也可以直接结束。',
  hints_disabled: '小测里不提供提示，可以直接写，也可以随时结束。',
}
/** Kind messages for the voluntary-activity 409s; other failures keep the shared safe message. */
export function practiceMessage(error: unknown, fallback: string): string {
  return error instanceof StudentError ? startMessages[error.code] ?? error.message : fallback
}
export const RESUME_NOTICE = '你还有一个没结束的活动，可以接着做，也可以直接结束。'
export const isActiveConflict = (error: unknown) => error instanceof StudentError && error.status === 409 && error.code === 'revision_conflict'
