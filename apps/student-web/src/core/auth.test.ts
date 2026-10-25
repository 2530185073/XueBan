import { describe, expect, it, vi } from 'vitest'
import { IDBFactory } from 'fake-indexeddb'
import { StudentClient } from './student'
import { DraftStore } from './store'
import { StudentApi } from './api'
import type { Account, IdentityContext } from './types'

class SessionStorage implements Storage {
  values = new Map<string, string>()
  get length() { return this.values.size }
  clear() { this.values.clear() }
  getItem(key: string) { return this.values.get(key) ?? null }
  key(index: number) { return [...this.values.keys()][index] ?? null }
  removeItem(key: string) { this.values.delete(key) }
  setItem(key: string, value: string) { this.values.set(key, value) }
}
const reply = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status })
const problem = (status: number, code: string) => reply({ type: 'urn:xueban:auth', title: '合成认证状态', status, category: status === 401 ? 'authorization' : 'validation', code, detail: '测试', request_id: 'request_auth', current_revision: null, retry_after_seconds: null }, status)
const secret = 'synthetic initial password'
const fresh = 'Fresh123'
function setup(required = false, admin = false) {
  const storage = new SessionStorage(), calls: { path: string; init: RequestInit }[] = []
  const account: Account = { username: 'synthetic.student', must_change_password: required, student_id: admin ? null : 'student_one' }
  const identity: IdentityContext = { actor_id: 'actor_account', roles: admin ? ['campus_admin'] : ['student'], authorized_students: admin ? [] : [{ student_id: 'student_one', campus_id: 'campus_one', display_name: '合成学生' }] }
  let loss = '', rejected = false
  const session = () => ({ access_token: account.must_change_password ? 'synthetic.initial.token' : 'synthetic.regular.token', expires_at: '2099-01-01T00:00:00Z', must_change_password: account.must_change_password })
  const transport = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const path = new URL(String(input)).pathname; calls.push({ path, init })
    if (path === loss) { loss = ''; throw new TypeError('synthetic network loss') }
    if (rejected) return problem(401, 'authentication_required')
    if (path === '/v1/auth/login') return reply(session())
    if (path === '/v1/auth/account') return reply(account)
    if (path === '/v1/auth/change-password') { account.must_change_password = false; return reply(session()) }
    if (path === '/v1/auth/logout') return reply({ logged_out: true })
    if (path === '/v1/me') return reply(identity)
    if (path === '/v1/admin/student-accounts') return reply({ items: [] })
    if (path === '/v1/students/student_one/homeworks') return reply({ items: [], next_cursor: null })
    throw new Error('Unexpected synthetic route')
  }) as unknown as typeof fetch
  const options = { origin: 'http://localhost', storage, transport, store: new DraftStore(new IDBFactory()) }
  return { options, client: new StudentClient(options), storage, calls, account, identity, lose: (path: string) => { loss = path }, revoke: () => { rejected = true } }
}

describe('long-term account authentication', () => {
  it('requires first password change before me or homework, including after refresh', async () => {
    const s = setup(true)
    expect(await s.client.login(' SYNTHETIC.STUDENT ', secret)).toBeNull()
    expect(s.client.account?.must_change_password).toBe(true)
    expect(s.calls.some(c => c.path === '/v1/me')).toBe(false)
    await expect(s.client.listDrafts()).rejects.toMatchObject({ code: 'password_change_required' })
    const next = new StudentClient(s.options); await next.restore()
    expect(next.account?.must_change_password).toBe(true); expect(next.identity).toBeNull()
    expect(s.calls.some(c => c.path === '/v1/me')).toBe(false)
    await next.changePassword(secret, fresh)
    expect(next.account?.must_change_password).toBe(false); expect(next.selectedStudentId).toBe('student_one')
    expect(s.calls.at(-1)?.path).toBe('/v1/me')
    const saved = [...s.storage.values.values()].join('')
    expect(saved).not.toContain(secret); expect(saved).not.toContain(fresh); expect(saved).not.toContain('synthetic.student')
    expect(saved).toContain('synthetic.regular.token')
  })
  it('ignores a retired pending pair and paired session instead of exchanging them', async () => {
    const s = setup()
    s.storage.setItem('xueban:http://localhost:pair', JSON.stringify({ code: 'retired-code', key: 'retired-key' }))
    s.storage.setItem('xueban:http://localhost:session', JSON.stringify({ access_token: 'retired-token', expires_at: '2099-01-01T00:00:00Z' }))
    expect(await s.client.restore()).toBeNull(); expect(s.calls).toHaveLength(0); expect(s.storage.length).toBe(0)
    await s.client.login('synthetic.student', secret); expect(s.client.selectedStudentId).toBe('student_one')
  })
  it('restores account authorization and never picks another authorized child', async () => {
    const s = setup(); s.identity.authorized_students.unshift({ student_id: 'student_other', campus_id: 'campus_one', display_name: '别的学生' })
    await s.client.login('synthetic.student', secret)
    expect(s.client.selectedStudentId).toBe('student_one')
    expect(() => s.client.selectStudent('student_other')).toThrow()
    const next = new StudentClient(s.options); await next.restore(); expect(next.selectedStudentId).toBe('student_one')
    await next.changePassword(secret, fresh); expect(next.selectedStudentId).toBe('student_one')
    expect(s.calls.filter(c => c.path === '/v1/auth/login')).toHaveLength(1)
  })
  it('supports an administrator with no fake student or draft authorization', async () => {
    const s = setup(false, true); await s.client.login('synthetic.admin', secret)
    expect(s.client.identity?.roles).toEqual(['campus_admin']); expect(s.client.selectedStudentId).toBeNull()
    expect(await s.client.adminRequest('/v1/admin/student-accounts')).toEqual({ items: [] })
    await expect(s.client.listDrafts()).rejects.toMatchObject({ status: 403 })
    expect(s.calls.some(c => c.path.startsWith('/v1/students/'))).toBe(false)
    await expect(s.client.adminRequest('/v1/auth/account')).rejects.toMatchObject({ code: 'invalid_path' })
    await expect(s.client.adminRequest('/v1/admin/student-accounts', { method: 'POST', body: {} })).rejects.toMatchObject({ code: 'invalid_request' })
  })
  it('sends logout to the server and clears the session even when its response is unknown', async () => {
    const s = setup(); await s.client.login('synthetic.student', secret)
    s.lose('/v1/auth/logout'); await expect(s.client.logout()).rejects.toMatchObject({ code: 'logout_unconfirmed' })
    expect(s.client.identity).toBeNull(); expect(s.client.account).toBeNull(); expect(s.storage.length).toBe(0)
    const writes = s.calls.filter(c => c.path === '/v1/auth/logout')
    expect(writes).toHaveLength(1); expect(writes[0].init.body).toBe('{}'); expect(writes[0].init.headers).toHaveProperty('Idempotency-Key')
    await s.client.login('synthetic.student', secret); await s.client.logout(); expect(s.storage.length).toBe(0)
  })
  it('accepts simple passwords for change and login', async () => {
    const s = setup(), password = 'abc123'
    await s.client.login('synthetic.student', password)
    await s.client.changePassword(password, 'xyz789')
    expect(JSON.parse(s.calls.find(c => c.path === '/v1/auth/login')!.init.body as string).password).toBe(password)
    expect(JSON.parse(s.calls.find(c => c.path === '/v1/auth/change-password')!.init.body as string).current_password).toBe(password)
    await expect(s.client.login('synthetic.student', '🙂'.repeat(129))).rejects.toMatchObject({ code: 'invalid_login' })
  })
  it('retains an unknown password change only in memory and replays the original key', async () => {
    const s = setup(true); await s.client.login('synthetic.student', secret)
    s.lose('/v1/auth/change-password'); await expect(s.client.changePassword(secret, fresh)).rejects.toMatchObject({ code: 'network_unknown' })
    await expect(s.client.changePassword(secret, fresh + '4')).rejects.toMatchObject({ code: 'auth_request_unknown' })
    await s.client.changePassword(secret, fresh)
    const writes = s.calls.filter(c => c.path === '/v1/auth/change-password')
    expect(writes).toHaveLength(2); expect(writes[0].init.headers).toEqual(writes[1].init.headers); expect(writes[0].init.body).toBe(writes[1].init.body)
    expect([...s.storage.values.values()].join('')).not.toContain('password')
  })
  it('does not expose cached authorization offline or after a revoked session', async () => {
    const s = setup(); await s.client.login('synthetic.student', secret)
    const next = new StudentClient(s.options); s.lose('/v1/auth/account')
    await expect(next.restore()).rejects.toMatchObject({ code: 'network_unknown' }); expect(next.identity).toBeNull(); expect(next.account).toBeNull()
    await next.restore(); s.revoke(); await expect(next.history()).rejects.toMatchObject({ status: 401 })
    expect(next.identity).toBeNull(); expect(next.account).toBeNull(); expect(s.storage.length).toBe(0)
  })
  it('keeps a valid session after a mistyped current password and exposes a safe error', async () => {
    const revoked = vi.fn()
    const api = new StudentApi('http://localhost', () => 'synthetic.token', revoked, vi.fn(async () => problem(401, 'invalid_credentials')) as unknown as typeof fetch)
    await expect(api.request('/v1/auth/change-password', 'AuthSession', '{}', 'synthetic-key')).rejects.toMatchObject({ code: 'invalid_credentials' })
    expect(revoked).not.toHaveBeenCalled()
  })
  it('rejects expired and malformed sessions without restoring old identity', async () => {
    const s = setup(); s.storage.setItem('xueban:http://localhost:session', JSON.stringify({ access_token: 'old', expires_at: '2000-01-01T00:00:00Z', kind: 'account' }))
    expect(await s.client.restore()).toBeNull(); expect(s.calls).toHaveLength(0)
    const api = new StudentApi('http://localhost', () => null, () => undefined, vi.fn(async () => reply({ access_token: 'synthetic', expires_at: '2099-01-01T00:00:00Z' })) as unknown as typeof fetch)
    await expect(api.request('/v1/auth/login', 'AuthSession', '{}', 'synthetic-key', true)).rejects.toMatchObject({ code: 'invalid_response' })
  })
})
