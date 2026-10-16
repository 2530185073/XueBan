import type { HelpRequest } from './api/contracts.generated'
import type { ReviewQueueItem, ReviewReason } from './api/types'

export type QueueTab = 'help' | 'review'
export type HelpFilter = 'all' | 'waiting' | 'claimed'

export interface Selection { caseId: string; helpId: string | null }

export interface QueueState<T> { items: T[]; cursor: string | null; loaded: boolean }
export const emptyQueue = <T>(): QueueState<T> => ({ items: [], cursor: null, loaded: false })

export const kindLabels: Record<HelpRequest['kind'], string> = { question: '作业答疑', recognition: '识别有误', judgment: '判断有疑问', appeal: '申请重查' }
export const helpStatusLabels: Record<HelpRequest['status'], string> = { waiting: '待接手', claimed: '处理中', resolved: '帮助已结束', unresolved: '仍需帮助', cancelled: '已取消' }
export const reasonLabels: Record<ReviewReason, string> = { verify_candidate: '核实 AI 候选', unable_to_judge: 'AI 无法判断', recognition_revision: '识别需修订', awaiting_check: '修订后待重新检查' }
export const progressLabels: Record<string, string> = { pending: '待检查', editing: '订正中', rechecking: '检查中', needs_correction: '需要订正', verified_resolved: '已核实解决', paused: '已暂停' }

/** Keep the order already on screen; refreshed items replace stale copies and new ones are appended. */
export function mergeById<T>(current: T[], incoming: T[], id: (item: T) => string): T[] {
  const fresh = new Map(incoming.map(item => [id(item), item]))
  const merged = current.map(item => fresh.get(id(item)) ?? item)
  const seen = new Set(current.map(id))
  for (const item of incoming) if (!seen.has(id(item))) { merged.push(item); seen.add(id(item)) }
  return merged
}

export function applyPage<T>(state: QueueState<T>, page: { items: T[]; next_cursor: string | null }, id: (item: T) => string, append: boolean): QueueState<T> {
  return { items: append ? mergeById(state.items, page.items, id) : page.items, cursor: page.next_cursor, loaded: true }
}

export const helpId = (help: HelpRequest) => help.id
export const reviewId = (item: ReviewQueueItem) => item.case_id

/** Cases that currently have a child waiting or being helped, keyed by case id. */
export function activeHelpByCase(helps: HelpRequest[]): Map<string, HelpRequest> {
  const result = new Map<string, HelpRequest>()
  for (const help of helps) if (help.status === 'waiting' || help.status === 'claimed') {
    const known = result.get(help.case_id)
    if (!known || Date.parse(help.created_at) > Date.parse(known.created_at)) result.set(help.case_id, help)
  }
  return result
}

export function filterHelp(items: HelpRequest[], filter: HelpFilter, search: string, names: Map<string, string>): HelpRequest[] {
  const term = search.trim()
  return items.filter(item => (filter === 'all' || item.status === filter) &&
    (!term || item.note.includes(term) || item.id.includes(term) || (names.get(item.case_id) ?? '').includes(term)))
}

export function filterReview(items: ReviewQueueItem[], search: string): ReviewQueueItem[] {
  const term = search.trim()
  return items.filter(item => !term || item.student_name.includes(term) || (item.homework_title ?? '').includes(term) || item.case_id.includes(term))
}

/** Appeals first, then by waiting time, so the longest-waiting child is on top. */
export function orderHelp(items: HelpRequest[]): HelpRequest[] {
  return [...items].sort((a, b) => Number(b.kind === 'appeal') - Number(a.kind === 'appeal') || Date.parse(a.created_at) - Date.parse(b.created_at))
}

export function isSelected(selection: Selection | null, caseId: string, help: string | null = null) {
  return !!selection && selection.caseId === caseId && (help === null || selection.helpId === help)
}
