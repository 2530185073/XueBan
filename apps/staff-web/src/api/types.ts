// Backend DTOs that are not part of contracts/phase1; validated by hand here.

export type ReviewReason = 'verify_candidate' | 'unable_to_judge' | 'recognition_revision' | 'awaiting_check' | 'ai_initial' | 'ai_pending' | 'staff_verified'

export interface ReviewQueueItem {
  case_id: string
  homework_id: string
  student_id: string
  student_name: string
  homework_title: string | null
  reason: ReviewReason
  progress: string
  updated_at: string
}

export interface ReviewQueuePage { items: ReviewQueueItem[]; next_cursor: string | null }

export interface AuthSession { access_token: string; expires_at: string; must_change_password: boolean }

export interface AccountInfo { username: string; must_change_password: boolean; student_id: string | null }

const ID = /^[a-z][a-z0-9_-]{2,63}$/
const reasons: ReviewReason[] = ['verify_candidate', 'unable_to_judge', 'recognition_revision', 'awaiting_check', 'ai_initial', 'ai_pending', 'staff_verified']
const record = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value)
const text = (value: unknown, max: number) => typeof value === 'string' && value.length > 0 && value.length <= max
const time = (value: unknown) => typeof value === 'string' && Number.isFinite(Date.parse(value))

export function isReviewQueueItem(value: unknown): value is ReviewQueueItem {
  return record(value) && typeof value.case_id === 'string' && /^case_[a-f0-9]{32}$/.test(value.case_id) &&
    typeof value.homework_id === 'string' && ID.test(value.homework_id) && typeof value.student_id === 'string' && ID.test(value.student_id) &&
    text(value.student_name, 200) && (value.homework_title === null || typeof value.homework_title === 'string' && value.homework_title.length <= 500) &&
    reasons.includes(value.reason as ReviewReason) && text(value.progress, 40) && time(value.updated_at)
}

export function isReviewQueuePage(value: unknown): value is ReviewQueuePage {
  return record(value) && Array.isArray(value.items) && value.items.length <= 100 && value.items.every(isReviewQueueItem) &&
    (value.next_cursor === null || typeof value.next_cursor === 'string' && /^case_[a-f0-9]{32}$/.test(value.next_cursor))
}

export function isAuthSession(value: unknown): value is AuthSession {
  return record(value) && text(value.access_token, 16384) && time(value.expires_at) && typeof value.must_change_password === 'boolean'
}

export function isAccountInfo(value: unknown): value is AccountInfo {
  return record(value) && text(value.username, 64) && typeof value.must_change_password === 'boolean' &&
    (value.student_id === null || typeof value.student_id === 'string')
}
