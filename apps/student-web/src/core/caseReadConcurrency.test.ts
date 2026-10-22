import { describe, expect, it } from 'vitest'
import { IDBFactory } from 'fake-indexeddb'
import examples from '../../../../contracts/phase1/examples.json'
import { StudentClient } from './student'
import { DraftStore } from './store'
import type { Attempt, CorrectionCase } from './types'

class MemorySession implements Storage {
  values = new Map<string, string>()
  get length() { return this.values.size }
  clear() { this.values.clear() }
  getItem(key: string) { return this.values.get(key) ?? null }
  key(index: number) { return [...this.values.keys()][index] ?? null }
  removeItem(key: string) { this.values.delete(key) }
  setItem(key: string, value: string) { this.values.set(key, value) }
}
const fixture = <T,>(name: string) => structuredClone(examples.entries.find(item => item.name === name)!.value) as T
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } })
const problem = (status: number, code: string) => json({ type: 'urn:xueban:authorization', title: 'Synthetic denial', status, category: 'authorization', code, detail: 'Synthetic', request_id: 'request_test', current_revision: null, retry_after_seconds: null }, status)
function deferred() { let resolve!: () => void; const promise = new Promise<void>(r => { resolve = r }); return { promise, resolve } }
async function until(test: () => boolean) { for (let i = 0; i < 100 && !test(); i++) await new Promise(resolve => setTimeout(resolve, 1)); expect(test()).toBe(true) }
function setup() {
  const calls: { path: string; method: string }[] = []
  const storage = new MemorySession(), store = new DraftStore(new IDBFactory())
  let serial = 0, revision = 1
  const makeCase = (student = 'a', id = 'case_one'): CorrectionCase => ({
    ...fixture<CorrectionCase>('resolved_case'), id, revision, scope: { tenant_id: 'tenant_one', campus_id: 'campus_one', student_id: 'student_' + student },
    question_id: 'question_one', root_attempt_id: 'attempt_one', current_attempt_id: 'attempt_one', current_question_revision: 1,
    current_decision: null, progress: 'pending', attempt_ids: ['attempt_one'], check_ids: [], hint_ids: [], help_request_ids: [], review_ids: [], lifecycle: 'active', replacement_case_ids: [],
  })
  const attempt: Attempt = { ...fixture<Attempt>('original_attempt'), id: 'attempt_one', case_id: 'case_one', question_id: 'question_one', root_attempt_id: 'attempt_one' }
  let caseHandler = async (student: string, id: string) => json(makeCase(student, id))
  let meDenied = false
  const transport: typeof fetch = async (input, init = {}) => {
    const pathname = new URL(String(input)).pathname, method = init.method ?? 'GET'
    calls.push({ path: pathname, method })
    const token = (init.headers as Record<string, string>)?.Authorization?.slice(7) ?? '', student = token.split('.')[1]
    if (pathname === '/v1/auth/login') {
      const name = JSON.parse(String(init.body)).username.split('.')[1]
      return json({ access_token: `synthetic.${name}.${++serial}`, expires_at: '2099-01-01T00:00:00Z', must_change_password: false })
    }
    if (pathname === '/v1/auth/account') return json({ username: 'synthetic.' + student, student_id: 'student_' + student, must_change_password: false })
    if (pathname === '/v1/auth/logout') return json({ logged_out: true })
    if (pathname === '/v1/me') return meDenied ? problem(403, 'access_denied') : json({ actor_id: 'actor_' + student, roles: ['student'], authorized_students: [{ student_id: 'student_' + student, campus_id: 'campus_one', display_name: 'Synthetic student' }] })
    if (/^\/v1\/cases\/case_(one|two)$/.test(pathname)) return caseHandler(student, pathname.split('/').at(-1)!)
    if (pathname === '/v1/attempts/attempt_one') return json({ record: attempt, current_revision: attempt.revision, currently_effective: true })
    if (pathname === '/v1/cases/case_one/recognition-rechecks/status') return json({ case_id: 'case_one', attempt_id: 'attempt_one', limit: 2, used: 0, remaining: 2, active_job_id: null, last_job_id: null, last_outcome: null, eligible: true, blocked_reason: null })
    throw new Error('Unexpected synthetic route')
  }
  const client = new StudentClient({ origin: 'http://localhost', transport, storage, store })
  return { client, calls, makeCase, login: (student = 'a') => client.login('synthetic.' + student, 'synthetic password only'),
    setCase: (handler: typeof caseHandler) => { caseHandler = handler }, setRevision: (value: number) => { revision = value }, denyMe: () => { meDenied = true },
    count: (path = '/v1/cases/case_one') => calls.filter(item => item.path === path).length }
}

describe('only in-flight same-session case reads are shared', () => {
  it('coalesces the actual case record and recovery-status consumers on a card mount', async () => {
    const s = setup(); await s.login(); const gate = deferred(), beforeMe = s.count('/v1/me')
    s.setCase(async (student, id) => { await gate.promise; return json(s.makeCase(student, id)) })
    const work = Promise.all([s.client.caseRecord('case_one'), s.client.recognitionRecoveryStatus('case_one', 'attempt_one')])
    await until(() => s.count() > 0); await new Promise(resolve => setTimeout(resolve, 5)); gate.resolve()
    const [record, status] = await work
    expect(record.case.id).toBe('case_one'); expect(record.attempts[0].record.id).toBe(status.attempt_id)
    expect(s.count()).toBe(1); expect(s.count('/v1/me') - beforeMe).toBe(1)
    expect(s.calls.filter(call => call.method !== 'GET').map(call => call.path)).toEqual(['/v1/auth/login'])
  })
  it('does not cache a completed case or authorization, including a newer revision', async () => {
    const s = setup(); await s.login(); const before = s.count('/v1/me')
    const first = await s.client.caseRecord('case_one'); s.setRevision(2)
    const second = await s.client.caseRecord('case_one')
    expect([first.case.revision, second.case.revision]).toEqual([1, 2]); expect(s.count()).toBe(2); expect(s.count('/v1/me') - before).toBe(2)
  })
  it('gives each consumer independent mutable response objects', async () => {
    const s = setup(); await s.login(); const gate = deferred()
    s.setCase(async () => { await gate.promise; return json(s.makeCase()) })
    const work = Promise.all([s.client.caseRecord('case_one'), s.client.caseRecord('case_one')])
    await until(() => s.count() > 0); gate.resolve(); const [one, two] = await work
    one.case.attempt_ids.push('attempt_other'); one.case.scope.student_id = 'student_other'
    expect(two.case.attempt_ids).toEqual(['attempt_one']); expect(two.case.scope.student_id).toBe('student_a')
  })
  it.each([401, 403])('shares a %s failure without swallowing denial or retaining the failed flight', async status => {
    const s = setup(); await s.login(); const gate = deferred()
    s.setCase(async () => { await gate.promise; return problem(status, status === 401 ? 'authentication_required' : 'access_denied') })
    const work = Promise.allSettled([s.client.caseRecord('case_one'), s.client.recognitionRecoveryStatus('case_one', 'attempt_one')])
    await until(() => s.count() > 0); gate.resolve(); const results = await work
    expect(s.count()).toBe(1); expect(results.every(result => result.status === 'rejected')).toBe(true)
    if (status === 401) { expect(s.client.identity).toBeNull(); await s.login() }
    s.setCase(async () => json(s.makeCase())); await s.client.caseRecord('case_one'); expect(s.count()).toBe(2)
  })
  it('keeps different case ids separate and checks ownership per caller', async () => {
    const s = setup(); await s.login()
    s.setCase(async (student, id) => json(s.makeCase(id === 'case_two' ? 'other' : student, id)))
    const results = await Promise.allSettled([s.client.caseRecord('case_one'), s.client.caseRecord('case_two')])
    expect(results[0].status).toBe('fulfilled'); expect(results[1]).toMatchObject({ status: 'rejected', reason: { code: 'access_denied' } })
    expect(s.count()).toBe(1); expect(s.count('/v1/cases/case_two')).toBe(1)
  })
  it('same-token restore cannot reuse an older flight or let it clear the new flight', async () => {
    const s = setup(); await s.login(); const oldGate = deferred(), newGate = deferred(); let call = 0
    s.setCase(async () => { const number = ++call; await (number === 1 ? oldGate : newGate).promise; return json(s.makeCase()) })
    const old = s.client.caseRecord('case_one').then(() => 'unexpected', error => error.code)
    await until(() => s.count() === 1); await s.client.restore()
    const fresh = s.client.caseRecord('case_one'); await until(() => s.count() === 2)
    oldGate.resolve(); expect(await old).toBe('session_changed')
    const joined = s.client.recognitionRecoveryStatus('case_one', 'attempt_one'); await new Promise(resolve => setTimeout(resolve, 5))
    newGate.resolve(); await Promise.all([fresh, joined]); expect(s.count()).toBe(2); expect(s.client.selectedStudentId).toBe('student_a')
  })
  it('late 401 after a different account signs in cannot revoke or supply the new case', async () => {
    const s = setup(); await s.login(); const oldGate = deferred()
    s.setCase(async student => { if (student === 'a') { await oldGate.promise; return problem(401, 'authentication_required') } return json(s.makeCase(student)) })
    const old = s.client.caseRecord('case_one').catch(error => error.code)
    await until(() => s.count() === 1); await s.login('b'); const fresh = await s.client.caseRecord('case_one')
    oldGate.resolve(); await old
    expect(fresh.case.scope.student_id).toBe('student_b'); expect(s.client.selectedStudentId).toBe('student_b'); expect(s.count()).toBe(2)
  })
  it('a new grant denial cannot use an existing case flight to avoid fresh authorization', async () => {
    const s = setup(); await s.login(); const gate = deferred()
    s.setCase(async () => { await gate.promise; return json(s.makeCase()) })
    const first = s.client.caseRecord('case_one').catch(error => error.code); await until(() => s.count() === 1)
    s.denyMe(); await expect(s.client.recognitionRecoveryStatus('case_one', 'attempt_one')).rejects.toMatchObject({ status: 403 })
    gate.resolve(); expect(await first).toBe('session_changed'); expect(s.count()).toBe(1); expect(s.client.identity).toBeNull()
  })
})
