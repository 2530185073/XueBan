import { describe, expect, it, vi } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { AdminConsole } from './AdminConsole'
import { StaffOperations, editable, listStaff, normalizeStaff } from './staff'
import type { CreateStaff, StaffAccount } from './staff'
import { duration, loadMetrics, localWindow, parseMetrics, percent, tally, TASK_STATUSES } from './metrics'
import type { AdminClient } from './types'

const secret = '  synthetic teacher secret  '
const input: CreateStaff = { username: ' Teacher.One ', display_name: ' 合成老师 ', campus_ids: ['campus_b', 'campus_a', 'campus_b'], roles: ['sitting_teacher'], initial_password: secret }
const teacher: StaffAccount = { principal_id: 'staff_synthetic', username: 'teacher.one', roles: ['sitting_teacher'], campus_ids: ['campus_a', 'campus_b'], active: true, must_change_password: true }
function client(handler: (path: string, options?: Parameters<AdminClient['adminRequest']>[1]) => Promise<unknown>) {
  const request = vi.fn(handler)
  return { value: { adminRequest: request } as AdminClient, request }
}
const empty = { count: 0, p50: null, p95: null, max: null }
const metrics = {
  campus_id: 'campus_a', window: { from: '2026-10-10T16:00:00+00:00', to: '2026-10-17T16:00:00+00:00' },
  tasks: { recognize_homework: { created: 3, statuses: { succeeded: 2, failed: 1 }, failure_codes: { provider_timeout: 1 }, provider_executions: 4, end_to_end_seconds: { count: 3, p50: 42, p95: 300, max: 300 }, provider_latency_seconds: empty } },
  model_candidates: { decisions: 10, verdicts: {}, unable_to_judge_share: { numerator: 1, denominator: 10, rate: 0.1 } },
  staff_reviews: { reviews: 4, actions: { confirm: 3, replace: 1 }, overturn_share: { numerator: 1, denominator: 4, rate: 0.25 } },
  help_requests: { created: 2, statuses: {}, wait_to_claim_seconds: empty, wait_to_complete_seconds: empty, unclaimed_in_window: 1 },
  current_backlog: { open_help_requests: 1, review_queue: { verify_candidate: 2 }, review_queue_total: 2 },
}

describe('staff accounts reuse the page-memory retry contract', () => {
  it('normalizes identity and campuses without touching the password', () => {
    const normalized = normalizeStaff(input)
    expect(normalized).toMatchObject({ username: 'teacher.one', display_name: '合成老师', campus_ids: ['campus_a', 'campus_b'] })
    expect(normalized.initial_password).toBe(secret)
    expect(() => normalizeStaff({ ...input, roles: ['data_steward'] })).toThrow('坐班老师')
    expect(() => normalizeStaff({ ...input, campus_ids: [] })).toThrow('校区')
  })
  it('retries an unknown creation with the identical body and key and refuses a second operation meanwhile', async () => {
    const api = client(async () => { if (api.request.mock.calls.length === 1) throw new Error('网络结果未知'); return teacher })
    const operations = new StaffOperations(api.value, () => 'synthetic-key')
    await expect(operations.create(input)).rejects.toThrow('网络结果未知')
    await expect(operations.create({ ...input, username: 'other.teacher' })).rejects.toThrow('上一项操作')
    expect(JSON.stringify(operations.pendingSummary)).not.toContain(secret)
    const receipt = await operations.retry()
    expect(api.request.mock.calls[0]).toEqual(api.request.mock.calls[1])
    expect(api.request.mock.calls[0][1]?.idempotencyKey).toBe('synthetic-key')
    expect(receipt).toEqual({ kind: 'create', label: '合成老师', username: 'teacher.one', password: secret })
  })
  it('rejects a creation response that does not match what was submitted', async () => {
    const api = client(async () => ({ ...teacher, campus_ids: ['campus_a'] }))
    const operations = new StaffOperations(api.value)
    await expect(operations.create(input)).rejects.toThrow('不一致')
    expect(operations.pendingSummary?.kind).toBe('create')
  })
  it('confirms a reset only from the reset response for that principal', async () => {
    const api = client(async () => ({ principal_id: 'someone_else', must_change_password: true }))
    const operations = new StaffOperations(api.value)
    await expect(operations.reset(teacher, secret)).rejects.toThrow('尚未得到确认')
    expect(api.request.mock.calls[0][0]).toBe('/v1/admin/staff-accounts/staff_synthetic/reset-password')
    expect(api.request.mock.calls[0][1]?.body).toEqual({ new_password: secret })
  })
  it('validates the staff list and rejects malformed roles', async () => {
    await expect(listStaff(client(async () => ({ items: [teacher], campuses: [{ campus_id: 'campus_a' }] })).value)).resolves.toMatchObject({ items: [teacher] })
    await expect(listStaff(client(async () => ({ items: [{ ...teacher, roles: [42] }], campuses: [] })).value)).rejects.toThrow()
  })
})

describe('service metrics', () => {
  it('requests whole local days within the 31-day limit', async () => {
    const now = new Date(2026, 9, 17, 13, 37)
    const window = localWindow(7, now)
    expect(window.from).toEqual(new Date(2026, 9, 11))
    expect(window.to).toEqual(new Date(2026, 9, 18))
    expect(() => localWindow(32, now)).toThrow()
    const api = client(async () => metrics)
    await loadMetrics(api.value, 'campus_a', window)
    const url = new URL(api.request.mock.calls[0][0], 'http://x')
    expect(url.pathname).toBe('/v1/admin/service-metrics')
    expect(url.searchParams.get('campus_id')).toBe('campus_a')
    expect(url.searchParams.get('from')).toBe(window.from.toISOString())
  })
  it('rejects a response for another campus or with malformed figures', () => {
    expect(parseMetrics(metrics, 'campus_a').current_backlog.review_queue_total).toBe(2)
    expect(() => parseMetrics(metrics, 'campus_b')).toThrow()
    expect(() => parseMetrics({ ...metrics, staff_reviews: { ...metrics.staff_reviews, overturn_share: { numerator: 1, denominator: 4, rate: 3 } } }, 'campus_a')).toThrow()
  })
  it('formats durations, shares and tallies for staff reading', () => {
    expect(duration(null)).toBe('—'); expect(duration(42)).toBe('42 秒'); expect(duration(300)).toBe('5 分钟'); expect(duration(5400)).toBe('1.5 小时')
    expect(percent({ numerator: 1, denominator: 4, rate: 0.25 })).toBe('25%（1/4）')
    expect(percent({ numerator: 0, denominator: 0, rate: null })).toBe('—')
    expect(tally(TASK_STATUSES, { succeeded: 2, odd: 1 })).toBe('成功 2，odd 1')
    expect(tally(TASK_STATUSES, {})).toBe('—')
  })
})

describe('admin console', () => {
  it('opens on student accounts and offers staff and metrics tabs', () => {
    const html = renderToStaticMarkup(<AdminConsole client={client(async () => ({ items: [], campuses: [] })).value} />)
    expect(html).toContain('学员账号'); expect(html).toContain('老师账号'); expect(html).toContain('运行指标')
    expect(html).toContain('aria-selected="true"')
    expect(html).toContain('student-accounts-title')
  })
})

describe('staff access changes', () => {
  const admin = { ...teacher, principal_id: 'admin_other', username: 'other.admin', roles: ['campus_admin'] }
  it('lists other administrators read-only instead of rejecting the list', async () => {
    const list = await listStaff(client(async () => ({ items: [teacher, admin], campuses: [{ campus_id: 'campus_a' }] })).value)
    expect(list.items.map(editable)).toEqual([true, false])
    await expect(new StaffOperations(client(async () => admin).value).updateAccess(admin, { campus_ids: ['campus_a'], roles: ['sitting_teacher'], active: false })).rejects.toThrow('不是坐班老师')
  })
  it('sends a normalized access change with one retry key and checks the echoed state', async () => {
    const updated = { ...teacher, campus_ids: ['campus_a'], roles: ['data_steward', 'sitting_teacher'], active: false }
    const api = client(async () => { if (api.request.mock.calls.length === 1) throw new Error('结果未知'); return updated })
    const operations = new StaffOperations(api.value, () => 'access-key')
    await expect(operations.updateAccess(teacher, { campus_ids: ['campus_a', 'campus_a'], roles: ['data_steward', 'sitting_teacher'], active: false })).rejects.toThrow('结果未知')
    expect(operations.pendingSummary).toEqual({ kind: 'access', label: 'teacher.one', username: 'teacher.one' })
    const result = await operations.retry()
    expect(api.request.mock.calls[0]).toEqual(api.request.mock.calls[1])
    expect(api.request.mock.calls[0][0]).toBe('/v1/admin/staff-accounts/staff_synthetic/access')
    expect(api.request.mock.calls[0][1]?.body).toEqual({ campus_ids: ['campus_a'], roles: ['data_steward', 'sitting_teacher'], active: false })
    expect(result).toEqual({ kind: 'access', label: 'teacher.one', account: updated })
  })
  it('refuses to remove every campus and treats a mismatched echo as unconfirmed', async () => {
    const operations = new StaffOperations(client(async () => ({ ...teacher, active: true })).value)
    await expect(operations.updateAccess(teacher, { campus_ids: [], roles: ['sitting_teacher'], active: true })).rejects.toThrow('至少保留')
    await expect(operations.updateAccess(teacher, { campus_ids: ['campus_a', 'campus_b'], roles: ['sitting_teacher'], active: false })).rejects.toThrow('不一致')
    expect(operations.pendingSummary?.kind).toBe('access')
  })
})
