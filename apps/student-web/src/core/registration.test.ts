import { afterEach, describe, expect, it, vi } from 'vitest'
import { IDBFactory } from 'fake-indexeddb'
import { StudentApi } from './api'
import { StudentClient } from './student'
import { DraftStore } from './store'
import * as studentCore from './index'
import { useStudentWorkspace } from '../useStudentWorkspace'
import type { RegistrationResult } from './types'

// Exercise the hook's asynchronous action lock/state without adding a DOM dependency.
const hookState = vi.hoisted(() => ({ active: false, cursor: 0, slots: [] as unknown[] }))
vi.mock('react', async importOriginal => {
  const actual = await importOriginal<typeof import('react')>()
  return {
    ...actual,
    useState: <T>(initial: T | (() => T)) => {
      if (!hookState.active) return actual.useState(initial)
      const index = hookState.cursor++
      if (!(index in hookState.slots)) hookState.slots[index] = typeof initial === 'function' ? (initial as () => T)() : initial
      return [hookState.slots[index], (next: T | ((previous: T) => T)) => { hookState.slots[index] = typeof next === 'function' ? (next as (previous: T) => T)(hookState.slots[index] as T) : next }]
    },
    useRef: <T>(initial: T) => {
      if (!hookState.active) return actual.useRef(initial)
      const index = hookState.cursor++
      if (!(index in hookState.slots)) hookState.slots[index] = { current: initial }
      return hookState.slots[index]
    },
    useCallback: (callback: (...args: never[]) => unknown, deps: unknown[]) => hookState.active ? callback : actual.useCallback(callback, deps),
    useEffect: (effect: () => void, deps?: unknown[]) => { if (!hookState.active) actual.useEffect(effect, deps) },
  }
})

class MemoryStorage implements Storage {
  values = new Map<string, string>()
  get length() { return this.values.size }
  clear() { this.values.clear() }
  getItem(key: string) { return this.values.get(key) ?? null }
  key(index: number) { return [...this.values.keys()][index] ?? null }
  removeItem(key: string) { this.values.delete(key) }
  setItem(key: string, value: string) { this.values.set(key, value) }
}
const reply = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status })
const problem = (status: number, code: string) => reply({ type: 'urn:xueban:auth', title: 'Synthetic registration', status, category: status === 409 ? 'conflict' : status === 503 ? 'service' : 'authorization', code, detail: 'Untrusted synthetic server detail', request_id: 'request_registration', current_revision: null, retry_after_seconds: null }, status)
const offered = { enabled: true, username_preview: '荣耀学员001', grades: [5, 6, 7, 8] }
const assigned: RegistrationResult = { username: '荣耀学员002', student_id: 'student_registered_002', grade: 5 }
const password = 'Abc123'
const authSession = { access_token: 'synthetic.changed.token', expires_at: '2099-01-01T00:00:00Z', must_change_password: false }
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>(r => { resolve = r }); return { promise, resolve } }

function setup() {
  const storage = new MemoryStorage(), store = new DraftStore(new IDBFactory())
  const reserve = vi.spyOn(store, 'reserve'), saveOperation = vi.spyOn(store, 'saveOperation')
  const calls: { path: string; init: RequestInit }[] = []
  // The synthetic server commits once per key, including when its response is lost.
  const accounts = new Map<string, { body: string; result: RegistrationResult }>()
  let options: unknown = offered, username = 'existing.student'
  let respond: (result: RegistrationResult) => Response | Promise<Response> = result => reply(result, 201)
  const authResponses = new Map<string, (init: RequestInit) => Response | Promise<Response>>()
  const transport = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const path = new URL(String(input)).pathname; calls.push({ path, init })
    if (authResponses.has(path)) return authResponses.get(path)!(init)
    if (path === '/v1/auth/registration') return reply(options)
    if (path === '/v1/auth/register') {
      const key = new Headers(init.headers).get('Idempotency-Key')!
      const body = String(init.body), previous = accounts.get(key)
      if (previous && previous.body !== body) return problem(409, 'idempotency_conflict')
      const result = previous?.result ?? { ...assigned, username: `荣耀学员${String(accounts.size + 2).padStart(3, '0')}`, grade: JSON.parse(body).grade }
      accounts.set(key, { body, result })
      return respond(result)
    }
    if (path === '/v1/auth/login') {
      username = JSON.parse(String(init.body)).username
      return reply({ access_token: 'synthetic.existing.token', expires_at: '2099-01-01T00:00:00Z', must_change_password: false })
    }
    if (path === '/v1/auth/account') return reply({ username, must_change_password: false, student_id: 'student_existing' })
    if (path === '/v1/auth/change-password') return reply(authSession)
    if (path === '/v1/me') return reply({ actor_id: 'actor_existing', roles: ['student'], authorized_students: [{ student_id: 'student_existing', campus_id: 'campus_existing', display_name: '合成学生' }] })
    throw new Error(`Unexpected synthetic route: ${path}`)
  })
  const client = new StudentClient({ origin: 'http://localhost', storage, store, transport })
  return { client, storage, store, reserve, saveOperation, calls, accounts, transport,
    setOptions: (value: unknown) => { options = value },
    setResponse: (value: typeof respond) => { respond = value },
    setAuthResponse: (path: string, handler: (init: RequestInit) => Response | Promise<Response>) => { authResponses.set(path, handler) },
    writes: () => calls.filter(call => call.path === '/v1/auth/register'),
  }
}
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); hookState.active = false; hookState.slots = [] })

describe('public student registration', () => {
  it('reads an advisory preview and accepts the 201 assigned username without logging in', async () => {
    const s = setup(), changed = vi.fn(); s.client.subscribe(changed)
    expect(await s.client.registrationOptions()).toEqual(offered)
    expect(await s.client.register(password, password, 5)).toEqual(assigned)
    expect(s.calls.map(call => call.path)).toEqual(['/v1/auth/registration', '/v1/auth/register'])
    expect(s.calls[0].init.method).toBe('GET')
    expect(s.calls[0].init.body).toBeUndefined()
    expect(new Headers(s.calls[0].init.headers).has('Idempotency-Key')).toBe(false)
    expect(s.calls[1].init.method).toBe('POST')
    expect(JSON.parse(String(s.calls[1].init.body))).toEqual({ password, confirm_password: password, grade: 5 })
    expect(new Headers(s.calls[1].init.headers).get('Idempotency-Key')).toBeTruthy()
    for (const { init } of s.calls) {
      expect(new Headers(init.headers).has('Authorization')).toBe(false)
      expect(init.credentials).toBe('omit'); expect(init.cache).toBe('no-store')
    }
    expect(s.client.account).toBeNull(); expect(s.client.identity).toBeNull(); expect(s.client.selectedStudentId).toBeNull()
    expect(changed).not.toHaveBeenCalled(); expect(s.storage.length).toBe(0)
    expect(s.reserve).not.toHaveBeenCalled(); expect(s.saveOperation).not.toHaveBeenCalled()
  })

  it('accepts the disabled options contract', async () => {
    const s = setup(); s.setOptions({ enabled: false, username_preview: null, grades: [] })
    expect(await s.client.registrationOptions()).toEqual({ enabled: false, username_preview: null, grades: [] })
  })

  it.each([5, 6, 7, 8])('submits grade %s without changing it', async grade => {
    const s = setup()
    expect(await s.client.register(password, password, grade)).toMatchObject({ grade })
    expect(JSON.parse(String(s.writes()[0].init.body)).grade).toBe(grade)
  })

  it.each(['123', 'abc', 'AbC123', '1'.repeat(15), 'a'.repeat(15)])('accepts ASCII password %s', async value => {
    const s = setup(); await s.client.register(value, value, 5)
    expect(s.writes()).toHaveLength(1)
  })

  it.each(['', '1', '12', 'a'.repeat(16), 'abc def', ' abc', 'abc ', 'abc!', '中文密码', '１２３', 'abc\n', 'abc\r', 'ab\tc', 'abc\0', 'abc🙂', null, 123])('rejects invalid password %j before transport', async value => {
    const s = setup()
    await expect(s.client.register(value as string, value as string, 5)).rejects.toMatchObject({ code: 'invalid_password', status: 422 })
    expect(s.transport).not.toHaveBeenCalled()
  })

  it.each(['', 'abc123', 'Abc123 ', 'Abc123\n', null, undefined, 123])('rejects mismatched confirmation %j before transport', async value => {
    const s = setup()
    await expect(s.client.register(password, value as string, 5)).rejects.toMatchObject({ code: 'password_mismatch', status: 422 })
    expect(s.transport).not.toHaveBeenCalled()
  })

  it.each([0, 4, 9, 5.5, NaN, Infinity, '5', null, undefined, true])('rejects invalid grade %j before transport', async value => {
    const s = setup()
    await expect(s.client.register(password, password, value as number)).rejects.toMatchObject({ code: 'invalid_grade', status: 422 })
    expect(s.transport).not.toHaveBeenCalled()
  })

  it('replays a lost successful response with the same body and key, creating one account', async () => {
    const s = setup()
    s.setResponse(() => { throw new TypeError('synthetic response lost after commit') })
    await expect(s.client.register(password, password, 5)).rejects.toMatchObject({ code: 'network_unknown' })
    expect(s.accounts.size).toBe(1)
    await expect(s.client.register('different', 'different', 5)).rejects.toMatchObject({ code: 'auth_request_unknown', status: 409 })
    await expect(s.client.register(password, password, 6)).rejects.toMatchObject({ code: 'auth_request_unknown', status: 409 })
    expect(s.writes()).toHaveLength(1)
    // Reading another preview must not discard the unresolved registration.
    await s.client.registrationOptions()
    s.setResponse(result => reply(result))
    expect(await s.client.register(password, password, 5)).toEqual(assigned)
    const writes = s.writes()
    expect(writes).toHaveLength(2)
    expect(writes[0].init.headers).toEqual(writes[1].init.headers)
    expect(writes[0].init.body).toBe(writes[1].init.body)
    expect(s.accounts.size).toBe(1); expect(s.storage.length).toBe(0)
    expect(s.reserve).not.toHaveBeenCalled(); expect(s.saveOperation).not.toHaveBeenCalled()
    // Confirmed results release credentials and let a new registration use a new key.
    await s.client.register('New123', 'New123', 6)
    expect(s.accounts.size).toBe(2)
    expect(new Headers(s.writes()[2].init.headers).get('Idempotency-Key')).not.toBe(new Headers(writes[0].init.headers).get('Idempotency-Key'))
  })

  it('coalesces simultaneous identical requests and blocks conflicting in-flight payloads', async () => {
    const s = setup(), gate = deferred<Response>()
    s.setResponse(() => gate.promise)
    const first = s.client.register(password, password, 5), second = s.client.register(password, password, 5)
    await expect(s.client.register(password, password, 8)).rejects.toMatchObject({ code: 'auth_request_unknown' })
    expect(s.writes()).toHaveLength(1)
    gate.resolve(reply(assigned))
    expect(await Promise.all([first, second])).toEqual([assigned, assigned])
    expect(s.accounts.size).toBe(1)
  })

  it.each([[409, 'idempotency_conflict'], [409, 'username_conflict'], [503, 'service_unavailable']] as const)('retains the original request after %s %s', async (status, code) => {
    const s = setup(); s.setResponse(() => problem(status, code))
    await expect(s.client.register(password, password, 5)).rejects.toMatchObject({ code, status })
    await expect(s.client.register(password, password, 6)).rejects.toMatchObject({ code: 'auth_request_unknown' })
    s.setResponse(result => reply(result))
    expect(await s.client.register(password, password, 5)).toEqual(assigned)
    expect(s.writes()[0].init.headers).toEqual(s.writes()[1].init.headers)
    expect(s.accounts.size).toBe(1)
  })

  it.each([400, 401, 422])('releases a definitive %s rejection so corrected input can be sent', async status => {
    const s = setup()
    s.setResponse(() => { s.accounts.clear(); return problem(status, status === 401 ? 'authentication_required' : 'invalid_request') })
    await expect(s.client.register(password, password, 5)).rejects.toMatchObject({ status })
    s.setResponse(result => reply(result))
    expect(await s.client.register('New123', 'New123', 6)).toMatchObject({ grade: 6 })
    expect(s.writes()[0].init.headers).not.toEqual(s.writes()[1].init.headers)
  })

  it('explains a disabled POST using the standard access_denied problem', async () => {
    const s = setup(); s.setResponse(() => problem(403, 'access_denied'))
    await expect(s.client.register(password, password, 5)).rejects.toMatchObject({ code: 'access_denied', status: 403, message: '临时注册暂未开放或注册校区不可用，请联系老师；恢复后请用原内容重试。' })
  })

  it.each([[429, 'login_rate_limited'], [403, 'access_denied']] as const)('recovers a lost committed result after a temporary %s without allocating another account', async (status, code) => {
    const s = setup()
    s.setResponse(() => { throw new TypeError('synthetic response lost after commit') })
    await expect(s.client.register(password, password, 5)).rejects.toMatchObject({ code: 'network_unknown' })
    expect(s.accounts.size).toBe(1)
    s.setResponse(() => problem(status, code))
    const error = await s.client.register(password, password, 5).catch(error => error)
    expect(error).toMatchObject({ code, status }); expect(error.message).not.toContain('刷新')
    await expect(s.client.register(password, password, 6)).rejects.toMatchObject({ code: 'auth_request_unknown' })
    await expect(s.client.register('Other123', 'Other123', 5)).rejects.toMatchObject({ code: 'auth_request_unknown' })
    expect(s.writes()).toHaveLength(2)
    s.setResponse(result => reply(result, 201))
    expect(await s.client.register(password, password, 5)).toEqual(assigned)
    const writes = s.writes()
    expect(writes).toHaveLength(3)
    for (const write of writes) {
      expect(write.init.headers).toEqual(writes[0].init.headers)
      expect(write.init.body).toBe(writes[0].init.body)
    }
    expect(s.accounts.size).toBe(1); expect(s.storage.length).toBe(0)
  })

  it.each(['live', 'expired', 'public_401'] as const)('preserves an existing %s login and its storage', async mode => {
    const s = setup(); await s.client.login('existing.student', 'legacy Unicode 密码')
    const account = s.client.account, identity = s.client.identity, scope = s.client.historyScope, saved = [...s.storage.values]
    const changed = vi.fn(); s.client.subscribe(changed)
    if (mode === 'expired') vi.spyOn(Date, 'now').mockReturnValue(Date.parse('2100-01-01T00:00:00Z'))
    await s.client.registrationOptions()
    if (mode === 'public_401') {
      s.setResponse(() => problem(401, 'authentication_required'))
      await expect(s.client.register(password, password, 5)).rejects.toMatchObject({ status: 401 })
    } else expect(await s.client.register(password, password, 5)).toEqual(assigned)
    expect(s.client.account).toBe(account); expect(s.client.identity).toBe(identity)
    expect(s.client.selectedStudentId).toBe('student_existing'); expect(s.client.historyScope).toBe(scope)
    expect([...s.storage.values]).toEqual(saved); expect(changed).not.toHaveBeenCalled()
    for (const { init } of s.calls.filter(call => ['/v1/auth/register', '/v1/auth/registration'].includes(call.path))) expect(new Headers(init.headers).has('Authorization')).toBe(false)
  })

  it('does not consult the token callback or revoke a session on a public request', async () => {
    const token = vi.fn(() => { throw new Error('Public request must not touch session') }), revoked = vi.fn()
    const api = new StudentApi('http://localhost', token, revoked, vi.fn(async () => problem(401, 'authentication_required')))
    await expect(api.request('/v1/auth/registration', 'RegistrationOptions', undefined, undefined, true)).rejects.toMatchObject({ status: 401 })
    expect(token).not.toHaveBeenCalled(); expect(revoked).not.toHaveBeenCalled()
  })
})

describe('strict registration response validation', () => {
  const invalidNames = ['荣耀学员01', '荣耀学员００１', '荣耀学员001a', '荣耀学员001\n', '荣耀学员001 ', ' 荣耀学员001', '荣耀学员' + '1'.repeat(61), '普通学员001', 'abc123', '', null, 123]
  const invalidOptions: unknown[] = [
    null, [], true, 'enabled', {}, { ...offered, enabled: 'true' },
    { enabled: true, grades: [5, 6, 7, 8] }, { enabled: true, username_preview: '荣耀学员001' },
    ...invalidNames.map(username_preview => ({ ...offered, username_preview })),
    ...[[], [5, 5], [4, 5], [5, 9], ['5'], [true], [5.5], null, '5,6,7,8'].map(grades => ({ ...offered, grades })),
    { enabled: false, username_preview: '荣耀学员001', grades: [] },
    { enabled: false, username_preview: null, grades: [5] },
    { enabled: false, grades: [] }, { ...offered, access_token: 'unexpected' },
  ]
  it.each(invalidOptions.map(value => [value]))('rejects malformed options %j', async value => {
    const s = setup(); s.setOptions(value)
    await expect(s.client.registrationOptions()).rejects.toMatchObject({ code: 'invalid_response', status: 502 })
  })

  const invalidResults: unknown[] = [
    null, [], true, 'registered', {},
    ...invalidNames.map(username => ({ ...assigned, username })),
    ...['', ' ', 'student one', 'student_1\n', 'student_1\0', '学生001', 'a'.repeat(65), null, 123].map(student_id => ({ ...assigned, student_id })),
    ...[4, 9, 5.5, '5', null, false].map(grade => ({ ...assigned, grade })),
    { username: assigned.username, student_id: assigned.student_id },
    { username: assigned.username, grade: 5 }, { student_id: assigned.student_id, grade: 5 },
    { ...assigned, access_token: 'unexpected' }, { ...assigned, password },
    // Even a supported grade must match the request that created the account.
    { ...assigned, grade: 6 },
  ]
  it.each(invalidResults.map(value => [value]))('rejects malformed results and preserves the retry key: %j', async value => {
    const s = setup(); s.setResponse(() => reply(value))
    await expect(s.client.register(password, password, 5)).rejects.toMatchObject({ code: 'invalid_response', status: 502 })
    s.setResponse(result => reply(result))
    expect(await s.client.register(password, password, 5)).toEqual(assigned)
    expect(s.writes()[0].init.headers).toEqual(s.writes()[1].init.headers)
    expect(s.accounts.size).toBe(1); expect(s.client.account).toBeNull(); expect(s.storage.length).toBe(0)
  })

  it('rejects invalid JSON without forgetting a potentially committed account', async () => {
    const s = setup(); s.setResponse(() => new Response('{broken'))
    await expect(s.client.register(password, password, 5)).rejects.toMatchObject({ code: 'invalid_response' })
    s.setResponse(result => reply(result)); await s.client.register(password, password, 5)
    expect(s.accounts.size).toBe(1); expect(s.writes()[0].init.headers).toEqual(s.writes()[1].init.headers)
  })

  it.each(['荣耀学员001', '荣耀学员1000', '荣耀学员' + '1'.repeat(60)])('accepts a generated username up to the 64-character boundary: %s', async username => {
    const s = setup(); s.setOptions({ ...offered, username_preview: username }); s.setResponse(result => reply({ ...result, username }))
    expect(await s.client.registrationOptions()).toMatchObject({ username_preview: username })
    expect(await s.client.register(password, password, 5)).toMatchObject({ username })
  })
})

describe('generated usernames in the existing login flow', () => {
  it.each(['荣耀学员001', '荣耀学员002', '荣耀学员1000', '荣耀学员' + '1'.repeat(60), ' EXISTING.STUDENT '])('accepts %s while retaining legacy password support', async username => {
    const s = setup(), legacyPassword = '🙂'.repeat(80)
    await s.client.login(username, legacyPassword)
    expect(JSON.parse(String(s.calls[0].init.body))).toEqual({ username: username.trim().toLowerCase(), password: legacyPassword })
    expect(s.client.selectedStudentId).toBe('student_existing')
  })
  it.each(['荣耀学员01', '荣耀学员００１', '荣耀学员001abc', '荣耀学员' + '1'.repeat(61), '荣 耀学员001', '普通学员001'])('rejects a username outside the exact generated format: %s', async username => {
    const s = setup()
    await expect(s.client.login(username, password)).rejects.toMatchObject({ code: 'invalid_login' })
    expect(s.transport).not.toHaveBeenCalled()
  })
})

describe('shared authWrite concurrency and session fences', () => {
  it.each([403, 429])('retains the existing login retry policy after %s', async status => {
    const s = setup()
    s.setAuthResponse('/v1/auth/login', () => problem(status, status === 403 ? 'access_denied' : 'login_rate_limited'))
    await expect(s.client.login('old.student', password)).rejects.toMatchObject({ status })
    s.setAuthResponse('/v1/auth/login', () => reply(authSession))
    await s.client.login('new.student', password)
    const writes = s.calls.filter(call => call.path === '/v1/auth/login')
    expect(writes).toHaveLength(2)
    expect(writes[0].init.headers).not.toEqual(writes[1].init.headers)
    expect(s.client.selectedStudentId).toBe('student_existing')
  })

  it.each([403, 429])('retains the existing password-change retry policy after %s', async status => {
    const s = setup(); await s.client.login('existing.student', password)
    s.setAuthResponse('/v1/auth/change-password', () => problem(status, status === 403 ? 'access_denied' : 'login_rate_limited'))
    await expect(s.client.changePassword(password, 'New123')).rejects.toMatchObject({ status })
    s.setAuthResponse('/v1/auth/change-password', () => reply(authSession))
    await s.client.changePassword(password, 'Other123')
    const writes = s.calls.filter(call => call.path === '/v1/auth/change-password')
    expect(writes).toHaveLength(2)
    expect(writes[0].init.headers).not.toEqual(writes[1].init.headers)
    expect(s.client.selectedStudentId).toBe('student_existing')
  })

  it('shares concurrent identical login writes while only the latest login can accept the session', async () => {
    const s = setup(), gate = deferred<Response>()
    s.setAuthResponse('/v1/auth/login', () => gate.promise)
    const results = Promise.allSettled([s.client.login('existing.student', password), s.client.login('existing.student', password)])
    await Promise.resolve()
    expect(s.calls.filter(call => call.path === '/v1/auth/login')).toHaveLength(1)
    gate.resolve(reply(authSession))
    const [first, second] = await results
    expect(first).toMatchObject({ status: 'rejected', reason: { code: 'session_changed' } })
    expect(second.status).toBe('fulfilled'); expect(s.client.selectedStudentId).toBe('student_existing')
  })

  it('shares concurrent password writes and installs their rotated session once', async () => {
    const s = setup(); await s.client.login('existing.student', password)
    const gate = deferred<Response>(); s.setAuthResponse('/v1/auth/change-password', () => gate.promise)
    const results = Promise.allSettled([s.client.changePassword(password, 'New123'), s.client.changePassword(password, 'New123')])
    await expect(s.client.changePassword(password, 'Other123')).rejects.toMatchObject({ code: 'auth_request_unknown' })
    expect(s.calls.filter(call => call.path === '/v1/auth/change-password')).toHaveLength(1)
    gate.resolve(reply(authSession))
    const [first, second] = await results
    expect(first.status).toBe('fulfilled')
    expect(second).toMatchObject({ status: 'rejected', reason: { code: 'session_changed' } })
    expect(s.client.identity?.actor_id).toBe('actor_existing')
    expect(s.storage.getItem('xueban:http://localhost:session')).toContain(authSession.access_token)
  })

  it.each([200, 401])('a late password-change %s cannot replace or revoke a newer login', async status => {
    const s = setup(); await s.client.login('existing.student', password)
    const gate = deferred<Response>(); s.setAuthResponse('/v1/auth/change-password', () => gate.promise)
    const old = s.client.changePassword(password, 'New123').catch(error => error)
    await Promise.resolve()
    await s.client.login('new.student', password)
    const account = s.client.account, identity = s.client.identity, saved = [...s.storage.values]
    gate.resolve(status === 200 ? reply(authSession) : problem(401, 'authentication_required'))
    expect(await old).toMatchObject({ code: status === 200 ? 'session_changed' : 'authentication_required' })
    expect(s.client.account).toBe(account); expect(s.client.identity).toBe(identity)
    expect([...s.storage.values]).toEqual(saved)
  })

  it.each([200, 401])('an old login %s does not delete a newer unresolved authWrite', async status => {
    const s = setup(), oldGate = deferred<Response>(), newGate = deferred<Response>()
    let requests = 0
    s.setAuthResponse('/v1/auth/login', () => ++requests === 1 ? oldGate.promise : newGate.promise)
    const old = s.client.login('old.student', password).catch(error => error)
    await Promise.resolve(); s.client.disconnect()
    const current = s.client.login('new.student', password).catch(error => error)
    await Promise.resolve()
    oldGate.resolve(status === 200 ? reply(authSession) : problem(401, 'invalid_credentials'))
    await old
    const retry = s.client.login('new.student', password)
    const results = Promise.allSettled([current, retry])
    await Promise.resolve()
    expect(requests).toBe(2)
    newGate.resolve(reply(authSession)); await results
    expect(s.client.selectedStudentId).toBe('student_existing')
  })

  it('keeps an uncertain registration independent of a later successful login', async () => {
    const s = setup(); s.setResponse(() => { throw new TypeError('synthetic lost response') })
    await expect(s.client.register(password, password, 5)).rejects.toMatchObject({ code: 'network_unknown' })
    await s.client.login('existing.student', password)
    const saved = [...s.storage.values], identity = s.client.identity
    s.setResponse(result => reply(result, 201)); await s.client.register(password, password, 5)
    expect(s.accounts.size).toBe(1); expect(s.writes()[0].init.headers).toEqual(s.writes()[1].init.headers)
    expect(s.client.identity).toBe(identity); expect([...s.storage.values]).toEqual(saved)
  })
})

describe('workspace registration action', () => {
  function mount(client: StudentClient) {
    vi.spyOn(studentCore, 'createStudentClient').mockReturnValue(client)
    vi.stubGlobal('window', { location: { hash: '' } })
    hookState.slots = []
    return () => {
      hookState.cursor = 0; hookState.active = true
      try { return useStudentWorkspace() } finally { hookState.active = false }
    }
  }

  it('exposes busy state, blocks duplicate submissions and returns the assigned account without login', async () => {
    const s = setup(), gate = deferred<Response>(), login = vi.spyOn(s.client, 'login'), render = mount(s.client)
    s.setResponse(() => gate.promise)
    let workspace = render()
    const first = workspace.register(password, password, 5)
    workspace = render()
    expect(workspace.busy).toBe('正在注册'); expect(workspace.error).toBeNull(); expect(workspace.sessionInitializing).toBe(false)
    expect(await workspace.register(password, password, 5)).toBeNull()
    gate.resolve(reply(assigned, 201))
    expect(await first).toEqual(assigned)
    workspace = render()
    expect(workspace.busy).toBe(''); expect(workspace.error).toBeNull()
    expect(workspace.identity).toBeNull(); expect(workspace.account).toBeNull(); expect(workspace.studentId).toBeNull()
    expect(login).not.toHaveBeenCalled(); expect(s.writes()).toHaveLength(1)
  })

  it('returns null and a safe error, releases busy and permits the same request to recover', async () => {
    const s = setup(), render = mount(s.client)
    s.setResponse(() => { throw new TypeError('synthetic lost response') })
    expect(await render().register(password, password, 5)).toBeNull()
    let workspace = render()
    expect(workspace.busy).toBe(''); expect(workspace.error).toContain('结果尚未确认')
    s.setResponse(result => reply(result, 201))
    expect(await workspace.register(password, password, 5)).toEqual(assigned)
    workspace = render()
    expect(workspace.busy).toBe(''); expect(workspace.error).toBeNull()
    expect(s.accounts.size).toBe(1); expect(s.writes()[0].init.headers).toEqual(s.writes()[1].init.headers)
  })
})
