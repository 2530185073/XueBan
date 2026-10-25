import { describe, expect, it, vi } from 'vitest'
import { IDBFactory } from 'fake-indexeddb'
import { isPracticeSolution, type PracticeSolution } from './learning'
import { session } from './learning.fixtures'
import { DraftStore } from './store'
import { StudentClient } from './student'

class MemoryStorage implements Storage {
  private values = new Map<string, string>()
  get length() { return this.values.size }
  clear() { this.values.clear() }
  getItem(key: string) { return this.values.get(key) ?? null }
  key(index: number) { return [...this.values.keys()][index] ?? null }
  removeItem(key: string) { this.values.delete(key) }
  setItem(key: string, value: string) { this.values.set(key, value) }
}

const solution: PracticeSolution = { item_id: 'item_1', answer_text: '5', solution_text: '从 2 开始，再加 3。\n2 + 3 = 5。' }
const sessionPath = '/v1/practice-sessions/practice_1'
const solutionPath = `${sessionPath}/items/item_1/solution`
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } })
const problem = (status: number, code: string) => json({ type: 'urn:xueban:auth', title: 'Synthetic', status, category: 'authorization', code, detail: 'Synthetic', request_id: 'request_one', current_revision: null, retry_after_seconds: null }, status)
const identity = (actor = 'actor_one', student = 'student_one') => ({ actor_id: actor, roles: ['student'], authorized_students: [{ student_id: student, display_name: '合成学生', campus_id: 'campus_one' }] })
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>(done => { resolve = done }); return { promise, resolve } }
type Route = (init: RequestInit) => Response | Promise<Response>

function harness(routes: Record<string, Route> = {}) {
  let profile = identity(), me: (() => Response | Promise<Response>) | undefined
  const calls: { path: string; init: RequestInit }[] = []
  const transport = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const path = new URL(String(input)).pathname; calls.push({ path, init })
    if (path === '/v1/auth/login') return json({ access_token: 'synthetic.access.token', expires_at: '2099-01-01T00:00:00Z', must_change_password: false })
    if (path === '/v1/auth/account') return json({ username: 'synthetic.student', student_id: profile.authorized_students[0].student_id, must_change_password: false })
    if (path === '/v1/me') return me ? me() : json(profile)
    if (path === '/v1/auth/logout') return json({ logged_out: true })
    const route = routes[`${init.method ?? 'GET'} ${path}`]
    if (route) return route(init)
    if (path === sessionPath && init.method === 'GET') return json(session)
    if (path === solutionPath && init.method === 'POST') return json(solution)
    throw new Error(`Unexpected synthetic route ${init.method} ${path}`)
  }) as typeof fetch
  const client = new StudentClient({ origin: 'http://localhost', storage: new MemoryStorage(), store: new DraftStore(new IDBFactory()), transport })
  return {
    client, calls, login: () => client.login('synthetic.student', 'synthetic password'),
    setIdentity: (actor: string, student: string) => { profile = identity(actor, student) },
    setMe: (next: typeof me) => { me = next },
  }
}

const malformedSolutions: unknown[] = [
  null, [], '5', {},
  { answer_text: '5', solution_text: '2 + 3 = 5' },
  { item_id: 'item_1', solution_text: '2 + 3 = 5' },
  { item_id: 'item_1', answer_text: '5' },
  { ...solution, item_id: '' }, { ...solution, item_id: 1 }, { ...solution, item_id: 'x'.repeat(201) },
  { ...solution, answer_text: null }, { ...solution, answer_text: 5 }, { ...solution, answer_text: 'x'.repeat(16001) },
  { ...solution, answer_text: ' ' }, { ...solution, solution_text: '' },
  { ...solution, solution_text: null }, { ...solution, solution_text: ['2 + 3 = 5'] }, { ...solution, solution_text: 'x'.repeat(4001) },
]

describe('practice solution response guard', () => {
  it('accepts the answer and steps as separate bounded strings', () => {
    expect(isPracticeSolution(solution)).toBe(true)
    expect(isPracticeSolution({ item_id: 'x'.repeat(200), answer_text: '5'.repeat(16000), solution_text: '步'.repeat(4000) })).toBe(true)
  })
  it('rejects missing fields, wrong types and oversized values', () => {
    for (const value of malformedSolutions) expect(isPracticeSolution(value)).toBe(false)
  })
})

describe('student-owned practice solutions', () => {
  it('verifies the session and posts an authenticated empty request for its item', async () => {
    const s = harness(); await s.login()
    expect(s.calls.some(call => call.path.endsWith('/solution'))).toBe(false)
    const start = s.calls.length
    expect(await s.client.practiceSolution('practice_1', 'item_1')).toEqual(solution)
    const calls = s.calls.slice(start)
    expect(calls.map(call => [call.init.method, call.path])).toEqual([
      ['GET', '/v1/me'], ['GET', sessionPath], ['POST', solutionPath],
    ])
    for (const call of calls) expect(new Headers(call.init.headers).get('Authorization')).toBe('Bearer synthetic.access.token')
    const sent = calls[2].init
    expect(sent.body).toBe('{}')
    expect(new Headers(sent.headers).get('Content-Type')).toBe('application/json')
    expect(new Headers(sent.headers).get('Idempotency-Key')).toBeTruthy()
    expect(sent).toMatchObject({ credentials: 'omit', redirect: 'error', cache: 'no-store' })
  })

  it('encodes session and item identifiers as individual URL segments', async () => {
    const sessionId = 'practice /?#one', itemId = 'item /?#one'
    const path = `/v1/practice-sessions/${encodeURIComponent(sessionId)}`
    const value = { ...solution, item_id: itemId }
    const s = harness({
      [`GET ${path}`]: () => json({ ...session, id: sessionId, items: [{ ...session.items[0], id: itemId }] }),
      [`POST ${path}/items/${encodeURIComponent(itemId)}/solution`]: () => json(value),
    })
    await s.login()
    expect(await s.client.practiceSolution(sessionId, itemId)).toEqual(value)
  })

  it('requires authentication before making any request', async () => {
    const s = harness()
    await expect(s.client.practiceSolution('practice_1', 'item_1')).rejects.toMatchObject({ code: 'authentication_required' })
    expect(s.calls).toHaveLength(0)
  })

  it.each([
    { name: 'foreign student', body: { ...session, student_id: 'other_student' }, itemId: 'item_1', code: 'access_denied' },
    { name: 'wrong session', body: { ...session, id: 'other_session' }, itemId: 'item_1', code: 'invalid_response' },
    { name: 'foreign item', body: session, itemId: 'other_item', code: 'access_denied' },
    { name: 'malformed session', body: { ...session, items: [{ id: 'item_1' }] }, itemId: 'item_1', code: 'invalid_response' },
  ])('rejects $name before requesting the solution', async ({ body, itemId, code }) => {
    const s = harness({ [`GET ${sessionPath}`]: () => json(body) }); await s.login()
    await expect(s.client.practiceSolution('practice_1', itemId)).rejects.toMatchObject({ code })
    expect(s.calls.some(call => call.path.endsWith('/solution'))).toBe(false)
  })

  it('rejects a response for a different item, including another item in the same session', async () => {
    const s = harness({ [`POST ${solutionPath}`]: () => json({ ...solution, item_id: 'item_2' }) }); await s.login()
    await expect(s.client.practiceSolution('practice_1', 'item_1')).rejects.toMatchObject({ code: 'invalid_response' })
  })

  it('rejects malformed solution payloads at the client boundary', async () => {
    let body: unknown
    const s = harness({ [`POST ${solutionPath}`]: () => json(body) }); await s.login()
    for (body of malformedSolutions) await expect(s.client.practiceSolution('practice_1', 'item_1')).rejects.toMatchObject({ code: 'invalid_response' })
  })

  it('stops before practice requests when the account loses authorization', async () => {
    const s = harness(); await s.login()
    s.setMe(() => problem(403, 'access_denied'))
    await expect(s.client.practiceSolution('practice_1', 'item_1')).rejects.toMatchObject({ code: 'account_unavailable' })
    expect(s.calls.some(call => call.path.startsWith('/v1/practice-sessions/'))).toBe(false)
    expect(s.client.selectedStudentId).toBeNull()
  })

  for (const phase of ['ownership read', 'solution response'] as const) {
    it.each(['account switch', 'same-account restore', 'logout'])(`rejects stale ${phase} after %s`, async mode => {
      const pendingResponse = deferred<Response>(), started = deferred<void>()
      const readingSession = phase === 'ownership read'
      const s = harness({
        [readingSession ? `GET ${sessionPath}` : `POST ${solutionPath}`]: () => { started.resolve(); return pendingResponse.promise },
      })
      await s.login()
      const pending = s.client.practiceSolution('practice_1', 'item_1').catch(error => error)
      await started.promise
      if (mode === 'account switch') { s.setIdentity('other_actor', 'other_student'); await s.login() }
      else if (mode === 'same-account restore') await s.client.restore()
      else await s.client.logout()
      pendingResponse.resolve(json(readingSession ? session : solution))
      expect(await pending).toMatchObject({ code: 'session_changed' })
      expect(s.calls.filter(call => call.path.endsWith('/solution'))).toHaveLength(readingSession ? 0 : 1)
      expect(s.client.selectedStudentId).toBe(mode === 'logout' ? null : mode === 'account switch' ? 'other_student' : 'student_one')
    })
  }

  it('does not revoke a new login when the old solution request returns 401', async () => {
    const response = deferred<Response>(), started = deferred<void>()
    const s = harness({ [`POST ${solutionPath}`]: () => { started.resolve(); return response.promise } })
    await s.login()
    const pending = s.client.practiceSolution('practice_1', 'item_1').catch(error => error)
    await started.promise
    s.setIdentity('other_actor', 'other_student'); await s.login()
    response.resolve(problem(401, 'authentication_required'))
    expect(await pending).toMatchObject({ code: 'authentication_required' })
    expect(s.client.identity?.actor_id).toBe('other_actor')
    expect(s.client.selectedStudentId).toBe('other_student')
  })
})
