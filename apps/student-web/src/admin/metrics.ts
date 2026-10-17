import { AdminError } from './api'
import type { AdminClient } from './types'

export interface Distribution { count: number; p50: number | null; p95: number | null; max: number | null }
export interface Ratio { numerator: number; denominator: number; rate: number | null }
export interface TaskMetrics { created: number; statuses: Record<string, number>; failure_codes: Record<string, number>; end_to_end_seconds: Distribution; provider_latency_seconds: Distribution }
export interface ServiceMetrics {
  campus_id: string
  window: { from: string; to: string }
  tasks: Record<string, TaskMetrics>
  model_candidates: { decisions: number; unable_to_judge_share: Ratio }
  staff_reviews: { reviews: number; actions: Record<string, number>; overturn_share: Ratio }
  help_requests: { created: number; unclaimed_in_window: number; wait_to_claim_seconds: Distribution; wait_to_complete_seconds: Distribution }
  current_backlog: { open_help_requests: number; review_queue_total: number; review_queue: Record<string, number> }
}

export const MAX_WINDOW_DAYS = 31
const DAY = 86_400_000

const object = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === 'object' && !Array.isArray(value)
const count = (value: unknown): value is number => Number.isInteger(value) && (value as number) >= 0
const seconds = (value: unknown): value is number | null => value === null || (typeof value === 'number' && Number.isFinite(value) && value >= 0)
const counts = (value: unknown): value is Record<string, number> => object(value) && Object.values(value).every(count)
const distribution = (value: unknown): value is Distribution => object(value) && count(value.count) && seconds(value.p50) && seconds(value.p95) && seconds(value.max)
const ratio = (value: unknown): value is Ratio => object(value) && count(value.numerator) && count(value.denominator) && (value.rate === null || (typeof value.rate === 'number' && value.rate >= 0 && value.rate <= 1))
const task = (value: unknown): value is TaskMetrics => object(value) && count(value.created) && counts(value.statuses) && counts(value.failure_codes) && distribution(value.end_to_end_seconds) && distribution(value.provider_latency_seconds)

export function parseMetrics(value: unknown, campusId: string): ServiceMetrics {
  const v = value as Record<string, any>
  if (!object(v) || v.campus_id !== campusId || !object(v.window) || typeof v.window.from !== 'string' || typeof v.window.to !== 'string'
    || !object(v.tasks) || !Object.values(v.tasks).every(task)
    || !object(v.model_candidates) || !count(v.model_candidates.decisions) || !ratio(v.model_candidates.unable_to_judge_share)
    || !object(v.staff_reviews) || !count(v.staff_reviews.reviews) || !counts(v.staff_reviews.actions) || !ratio(v.staff_reviews.overturn_share)
    || !object(v.help_requests) || !count(v.help_requests.created) || !count(v.help_requests.unclaimed_in_window) || !distribution(v.help_requests.wait_to_claim_seconds) || !distribution(v.help_requests.wait_to_complete_seconds)
    || !object(v.current_backlog) || !count(v.current_backlog.open_help_requests) || !count(v.current_backlog.review_queue_total) || !counts(v.current_backlog.review_queue))
    throw new AdminError('运行指标返回不完整，请稍后重试。')
  return v as unknown as ServiceMetrics
}

/** Whole local days, newest last: [start of first day, start of the day after the last). */
export function localWindow(days: number, now = new Date()): { from: Date; to: Date } {
  if (!Number.isInteger(days) || days < 1 || days > MAX_WINDOW_DAYS) throw new AdminError(`统计范围需要在 1–${MAX_WINDOW_DAYS} 天之间。`)
  const to = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1)
  const from = new Date(to.getFullYear(), to.getMonth(), to.getDate() - days)
  return { from, to }
}

export async function loadMetrics(client: AdminClient, campusId: string, window: { from: Date; to: Date }): Promise<ServiceMetrics> {
  if (!campusId) throw new AdminError('请选择校区。')
  if (!(window.from < window.to) || window.to.getTime() - window.from.getTime() > MAX_WINDOW_DAYS * DAY + 3_600_000) throw new AdminError(`统计范围需要在 1–${MAX_WINDOW_DAYS} 天之间。`)
  const query = new URLSearchParams({ campus_id: campusId, from: window.from.toISOString(), to: window.to.toISOString() })
  return parseMetrics(await client.adminRequest<unknown>(`/v1/admin/service-metrics?${query}`), campusId)
}

export function duration(value: number | null): string {
  if (value === null) return '—'
  if (value < 60) return `${Math.round(value)} 秒`
  if (value < 3600) return `${Math.round(value / 60)} 分钟`
  return `${(value / 3600).toFixed(1)} 小时`
}

export function percent(value: Ratio): string {
  return value.rate === null ? '—' : `${Math.round(value.rate * 100)}%（${value.numerator}/${value.denominator}）`
}

export const TASK_KINDS: Record<string, string> = {
  recognize_homework: '识别作业', recognize_attempt: '识别订正', check: '判断订正', hint: '生成提示',
  repair_layout: '重切题目', generate_practice: '生成练习', project_student: '更新学习档案', agent_summary: '学习助理总结',
}
export const QUEUE_REASONS: Record<string, string> = {
  unable_to_judge: '模型无法判断', verify_candidate: '待核对判定', recognition_revision: '识别待修正', awaiting_check: '订正待检查',
}
export const TASK_STATUSES: Record<string, string> = {
  queued: '排队', running: '运行中', succeeded: '成功', failed: '失败', retryable_failed: '待重试', superseded: '已被替代', cancelled: '已取消',
}
export const REVIEW_ACTIONS: Record<string, string> = { confirm: '确认', replace: '改判', retract: '撤回' }
export const tally = (labels: Record<string, string>, values: Record<string, number>) =>
  Object.entries(values).map(([key, n]) => `${label(labels, key)} ${n}`).join('，') || '—'
export const label = (labels: Record<string, string>, key: string) => labels[key] ?? key
