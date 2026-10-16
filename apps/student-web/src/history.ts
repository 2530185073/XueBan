import type { CaseCounts, Coverage, HomeworkSession, HomeworkSummary } from './core/types'

export const UNLABELED_SUBJECT = '未标注科目'
export type StatusFilter = 'all' | 'needs_correction' | 'pending_review' | 'done'
export const statusFilters: { value: StatusFilter; label: string }[] = [{ value: 'all', label: '全部' }, { value: 'needs_correction', label: '有待订正' }, { value: 'pending_review', label: '待老师核实' }, { value: 'done', label: '已完成' }]
export interface HistoryFilter { subject: string | null; status: StatusFilter }
export interface DayGroup { key: string; label: string; items: HomeworkSession[] }

export const subjectOf = (h: HomeworkSession) => h.curriculum.subject?.trim() || UNLABELED_SUBJECT
export function subjects(items: HomeworkSession[]): string[] {
  const all = [...new Set(items.map(subjectOf))]
  return all.filter(s => s !== UNLABELED_SUBJECT).sort((a, b) => a.localeCompare(b, 'zh-CN')).concat(all.includes(UNLABELED_SUBJECT) ? [UNLABELED_SUBJECT] : [])
}
/** Status tags for one summary; a homework can be both 有待订正 and 待老师核实. 已完成 needs at least one question. */
export function statusesOf(counts: CaseCounts): Exclude<StatusFilter, 'all'>[] {
  const out: Exclude<StatusFilter, 'all'>[] = []
  if (counts.needs_correction) out.push('needs_correction')
  if (counts.pending_review) out.push('pending_review')
  if (!out.length && counts.total > 0) out.push('done')
  return out
}
/** A status filter only keeps homeworks whose counts are known; unknown ones are reported separately by the caller. */
export function filterHistory(items: HomeworkSession[], summaries: Record<string, HomeworkSummary | undefined>, filter: HistoryFilter): HomeworkSession[] {
  return items.filter(h => {
    if (filter.subject && subjectOf(h) !== filter.subject) return false
    if (filter.status === 'all') return true
    const summary = summaries[h.id]
    return !!summary && statusesOf(summary.counts).includes(filter.status)
  })
}
function dayKey(value: string, timeZone?: string) {
  const date = new Date(value); if (Number.isNaN(date.getTime())) return 'unknown'
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone, year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(date)
  const get = (type: string) => parts.find(p => p.type === type)?.value ?? ''
  return `${get('year')}-${get('month')}-${get('day')}`
}
/** Groups by the local calendar day of creation, keeping the server's newest-first order. */
export function groupByDay(items: HomeworkSession[], now = new Date(), timeZone?: string): DayGroup[] {
  const today = dayKey(now.toISOString(), timeZone), yesterday = dayKey(new Date(now.getTime() - 86_400_000).toISOString(), timeZone), groups: DayGroup[] = []
  for (const item of items) {
    const key = dayKey(item.created_at, timeZone)
    let group = groups.find(g => g.key === key)
    if (!group) {
      const label = key === 'unknown' ? '时间待确认' : key === today ? '今天' : key === yesterday ? '昨天' : new Intl.DateTimeFormat('zh-CN', { timeZone, month: 'long', day: 'numeric', weekday: 'short', ...(key.slice(0, 4) !== today.slice(0, 4) ? { year: 'numeric' } : {}) }).format(new Date(item.created_at))
      group = { key, label, items: [] }; groups.push(group)
    }
    group.items.push(item)
  }
  return groups
}
/** Counts only what was photographed this time; never an accuracy percentage for the whole homework. */
export function countsSentence(counts: CaseCounts): string {
  if (!counts.total) return '本次还没有读到题目'
  const parts = [`需订正 ${counts.needs_correction}`, `待核实 ${counts.pending_review}`]
  if (counts.candidate_correct) parts.push(`AI初判正确 ${counts.candidate_correct}`)
  parts.push(`已确认正确 ${counts.confirmed_correct}`)
  return `本次拍到 ${counts.total} 题：${parts.join('，')}`
}
export function coverageNote(coverage: Coverage): string | null {
  if (coverage.collection_scope === 'declared_complete') return coverage.note
  return ['只统计这次拍到并读出的题目，不代表整份作业的完成情况。', coverage.note].filter(Boolean).join(' ')
}
/** Most recent homework with something to correct, falling back to one awaiting verification. */
export function continueTarget(items: HomeworkSession[], summaries: Record<string, HomeworkSummary | undefined>): { homeworkId: string; caseId: string } | null {
  const pick = (key: 'needs_correction' | 'pending_review') => items.find(h => { const s = summaries[h.id]; return s && s.counts[key] > 0 && s.firstUnresolvedCaseId })
  const hit = pick('needs_correction') ?? pick('pending_review')
  return hit ? { homeworkId: hit.id, caseId: summaries[hit.id]!.firstUnresolvedCaseId! } : null
}
