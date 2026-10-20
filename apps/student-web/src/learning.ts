import { MAX_PRACTICE_ITEMS, type AgentContext, type AgentTip, type ChapterRollup, type EvidenceResult, type ExitReason, type KnowledgeGraphEdge, type KnowledgeGraphNode, type KnowledgeMap, type KnowledgeState, type LearningStatus, type Memory, type OptionalActivities, type PracticeResult, type PracticeSession, type PracticeStatus, type PreviewOffer, type VariantOffer } from './core/learning'
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
export const PENDING_NOTICE = '新的作答正在整理，知识地图更新中。更新完成前，先不显示旧判断。'
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

/** Pending is an invalidation boundary, not evidence that the student forgot. */
export function knowledgeNodes(map: KnowledgeMap): KnowledgeGraphNode[] {
  if (!['publisher_latest_reviewed', 'source_verified_ai_reviewed'].includes(map.graph_review_status ?? '')) return []
  return (map.nodes ?? []).filter(node => ['concept', 'skill'].includes(node.node_type)).map(node => map.projection.pending ? {
    ...node, learning_status: 'no_judgment', reason_codes: [], valid_evidence_count: 0,
    model_evidence_count: 0, staff_verified_evidence_count: 0, assessment_source: 'none',
  } : node)
}

export interface KnowledgeChapter { id: string; name: string; nodes: KnowledgeGraphNode[] }
export function knowledgeChapters(nodes: KnowledgeGraphNode[]): KnowledgeChapter[] {
  const chapters = new Map<string, KnowledgeChapter>()
  for (const node of [...nodes].sort((a, b) => a.sort_order - b.sort_order || a.id.localeCompare(b.id))) {
    const id = node.chapter_id ?? 'unassigned'
    const chapter = chapters.get(id) ?? { id, name: node.chapter_name || '其他知识点', nodes: [] }
    chapter.nodes.push(node); chapters.set(id, chapter)
  }
  return [...chapters.values()]
}

export const GRAPH_WIDTH = 660
export const GRAPH_NODE_WIDTH = 192
export const GRAPH_NODE_HEIGHT = 102
export interface PositionedKnowledge { node: KnowledgeGraphNode; x: number; y: number }
/** Stable prerequisite layers, not a force simulation or an invented learning path. */
export function layoutKnowledgeGraph(nodes: KnowledgeGraphNode[], edges: KnowledgeGraphEdge[]) {
  const ordered = [...nodes].sort((a, b) => a.sort_order - b.sort_order || a.id.localeCompare(b.id))
  const ids = new Set(ordered.map(node => node.id))
  const unique = new Set<string>()
  const visibleEdges = edges.filter(edge => {
    const key = `${edge.from_id}\0${edge.to_id}`
    if (edge.relation_type !== 'prerequisite_of' || !ids.has(edge.from_id) || !ids.has(edge.to_id) || edge.from_id === edge.to_id || unique.has(key)) return false
    unique.add(key); return true
  })
  const indegree = new Map(ordered.map(node => [node.id, 0]))
  const outgoing = new Map<string, string[]>()
  for (const edge of visibleEdges) {
    indegree.set(edge.to_id, (indegree.get(edge.to_id) ?? 0) + 1)
    outgoing.set(edge.from_id, [...(outgoing.get(edge.from_id) ?? []), edge.to_id])
  }
  const ranks = new Map(ordered.map(node => [node.id, 0]))
  const queue = ordered.filter(node => !indegree.get(node.id)).map(node => node.id)
  for (let i = 0; i < queue.length; i++) {
    for (const next of outgoing.get(queue[i]) ?? []) {
      ranks.set(next, Math.max(ranks.get(next) ?? 0, (ranks.get(queue[i]) ?? 0) + 1))
      indegree.set(next, (indegree.get(next) ?? 1) - 1)
      if (!indegree.get(next)) queue.push(next)
    }
  }
  // Cycles do not hang rendering or create status propagation.
  const layers = new Map<number, KnowledgeGraphNode[]>()
  for (const node of ordered) {
    const rank = ranks.get(node.id) ?? 0
    layers.set(rank, [...(layers.get(rank) ?? []), node])
  }
  const positions: PositionedKnowledge[] = []
  let y = 28
  for (const [, layer] of [...layers].sort(([a], [b]) => a - b)) {
    for (let offset = 0; offset < layer.length; offset += 3) {
      const row = layer.slice(offset, offset + 3)
      const left = (GRAPH_WIDTH - row.length * GRAPH_NODE_WIDTH - (row.length - 1) * 24) / 2
      row.forEach((node, index) => positions.push({ node, x: left + index * (GRAPH_NODE_WIDTH + 24), y }))
      y += GRAPH_NODE_HEIGHT + 54
    }
  }
  const byId = new Map(positions.map(point => [point.node.id, point]))
  const paths = visibleEdges.map(edge => {
    const a = byId.get(edge.from_id)!, b = byId.get(edge.to_id)!
    const fromX = a.x + GRAPH_NODE_WIDTH / 2, toX = b.x + GRAPH_NODE_WIDTH / 2
    const fromY = a.y + GRAPH_NODE_HEIGHT, toY = b.y
    return { edge, path: `M ${fromX} ${fromY} C ${fromX} ${fromY + 28}, ${toX} ${toY - 28}, ${toX} ${toY}` }
  })
  return { positions, paths, width: GRAPH_WIDTH, height: Math.max(184, y - 26) }
}

export function nodeNameLines(name: string): string[] {
  const chars = Array.from(name)
  if (chars.length <= 10) return [name]
  return [chars.slice(0, 10).join(''), chars.slice(10, chars.length > 20 ? 19 : 20).join('') + (chars.length > 20 ? '…' : '')]
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
export const readingDone = { title: '预习看完啦', text: '先有个印象就好，上课时再认真听。' }
export const exitChoices: { reason: ExitReason; label: string }[] = [
  { reason: 'tired', label: '有点累了' }, { reason: 'not_now', label: '现在不想做' }, { reason: 'done', label: '做得差不多了' }, { reason: 'other', label: '就是想结束' },
]
export const answerFeedback: Record<PracticeResult, { title: string; text: string; tone: Tone }> = {
  correct: { title: '做对了', text: '可以看看解析，确认自己的思路。', tone: 'green' },
  incorrect: { title: '这次没对上，没关系', text: '对照解析看看哪一步不一样，想明白就是收获。', tone: 'amber' },
  unscorable: { title: '暂时没有确认对错', text: '这次作答已保留，没有记成答错；可以对照解析看看思路。', tone: 'neutral' },
}
export const choiceLetters = ['A', 'B', 'C', 'D'] as const

const startMessages: Record<string, string> = {
  optional_gate_closed: '现在先不开始额外活动。等学校任务完成、老师开放时间后才可以选。今天就到这里也很好。',
  practice_not_needed: '这个知识点现在不需要额外练习，休息一下也很好。',
  course_unknown: '老师还没告诉学伴下一课学什么，预习先不开放。',
  no_reviewed_items: '这类题暂时没有可用的变式题，可以先回到自己的作业。',
  curriculum_mismatch: '这道题来自之前的教材，暂不放进当前教材的练习。原来的作答记录仍会保留。',
  unsupported_source_type: '这道原题的题型暂时还不能可靠识别，可以先回到自己的作业。',
  stale_source: '原题记录已经更新，请刷新后从最新的错题开始。',
  source_not_eligible: '这道题目前不需要额外生成练习，可以先回到自己的作业。',
  generation_not_ready: '这道题还没有完成核验，请稍后刷新查看。',
  assessment_pending: '这道题的上次作答正在核验，已提交的答案会保留，请等待结果。',
  practice_active: '你还有一个没结束的活动，可以接着做，也可以直接结束。',
  hints_disabled: '小测里不提供提示，可以直接写，也可以随时结束。',
}
/** Kind messages for the voluntary-activity 409s; other failures keep the shared safe message. */
export function practiceMessage(error: unknown, fallback: string): string {
  return error instanceof StudentError ? startMessages[error.code] ?? error.message : fallback
}
export const RESUME_NOTICE = '你还有一个没结束的活动，可以接着做，也可以直接结束。'
export const isActiveConflict = (error: unknown) => error instanceof StudentError && error.status === 409 && error.code === 'practice_active'
