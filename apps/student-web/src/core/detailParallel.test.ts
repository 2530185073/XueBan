import { expect, it } from 'vitest'
import { IDBFactory } from 'fake-indexeddb'
import examples from '../../../../contracts/phase1/examples.json'
import { StudentClient } from './student'
import { DraftStore } from './store'
import type { Attempt, CapturedQuestionRevision, CorrectionCase, HomeworkSession } from './types'

class MemorySession implements Storage {
  private values = new Map<string, string>()
  get length() { return this.values.size }
  clear() { this.values.clear() }
  getItem(key: string) { return this.values.get(key) ?? null }
  key(index: number) { return [...this.values.keys()][index] ?? null }
  removeItem(key: string) { this.values.delete(key) }
  setItem(key: string, value: string) { this.values.set(key, value) }
}
const fixture = <T>(name: string) => structuredClone(examples.entries.find(x => x.name === name)!.value) as T
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } })
const tick = () => new Promise<void>(resolve => setTimeout(resolve, 0))
const deferred = () => { let resolve!: () => void; const promise = new Promise<void>(r => { resolve = r }); return { promise, resolve } }

function harness(count = 8) {
  const storage = new MemorySession(), store = new DraftStore(new IDBFactory())
  let principal = 'one'
  const scope = { tenant_id: 'tenant_one', campus_id: 'campus_one', student_id: 'student_one' }
  const hw: HomeworkSession = { ...fixture<HomeworkSession>('homework'), id: 'homework_one', scope, asset_ids: [], question_ids: [], case_ids: [], job_ids: [] }
  const cases: CorrectionCase[] = [], questions: CapturedQuestionRevision[] = [], attempts: Attempt[] = []
  for (let i = 0; i < count; i++) {
    const c: CorrectionCase = { ...fixture<CorrectionCase>('resolved_case'), id: `case_${i}`, scope: { ...scope }, homework_id: hw.id, question_id: `question_${i}`, current_question_revision: 1, root_attempt_id: `attempt_${i}`, current_attempt_id: `attempt_${i}`, progress: 'pending', current_decision: null, attempt_ids: [`attempt_${i}`], check_ids: [], hint_ids: [], help_request_ids: [], review_ids: [], lifecycle: 'active', replacement_case_ids: [] }
    const q: CapturedQuestionRevision = { ...fixture<CapturedQuestionRevision>('question_revision_2'), question_id: c.question_id, revision: 1, homework_id: hw.id }
    const a: Attempt = { ...fixture<Attempt>('original_attempt'), id: c.current_attempt_id, case_id: c.id, question_id: c.question_id, root_attempt_id: c.current_attempt_id }
    cases.push(c); questions.push(q); attempts.push(a); hw.case_ids.push(c.id); hw.question_ids.push(q.question_id)
  }
  const calls: string[] = [], completed: string[] = []
  let active = 0, peak = 0
  let delay: (kind: string, index: number) => Promise<Response | void> = async () => undefined
  const transport: typeof fetch = async (input, init = {}) => {
    const path = new URL(String(input)).pathname; calls.push(path)
    if (path === '/v1/auth/login') return json({ access_token: `synthetic-token-${principal}`, expires_at: '2099-01-01T00:00:00Z', must_change_password: false })
    if (path === '/v1/auth/account') return json({ username: `synthetic.${principal}`, student_id: `student_${principal}`, must_change_password: false })
    if (path === '/v1/auth/logout') return json({ logged_out: true })
    if (path === '/v1/me') return json({ actor_id: `actor_${principal}`, roles: ['student'], authorized_students: [{ student_id: `student_${principal}`, display_name: '合成学生', campus_id: 'campus_one' }] })
    if (init.method === 'POST') throw new Error('Unexpected business write')
    if (path === '/v1/homeworks/homework_one') return json(hw)
    const match = path.match(/^\/v1\/(cases|questions|attempts)\/\w+_(\d+)$/)
    if (!match) throw new Error(`Unexpected synthetic path ${path}`)
    const [, kind, n] = match, index = Number(n)
    if (kind === 'cases') return json(cases[index])
    active++; peak = Math.max(active, peak)
    try {
      const response = await delay(kind, index)
      if (response) return response
      completed.push(path)
      return json({ record: kind === 'questions' ? questions[index] : attempts[index], current_revision: 1, currently_effective: true })
    } finally { active-- }
  }
  const client = new StudentClient({ origin: 'http://localhost', storage, store, transport })
  const login = () => client.login('synthetic.student', 'synthetic password only')
  return { client, store, calls, completed, cases, questions, attempts, hw, login, get peak() { return peak }, setDelay: (value: typeof delay) => { delay = value }, setPrincipal: (value: string) => { principal = value }, scope: (student = 'one') => JSON.stringify(['http://localhost', `actor_${student}`, `student_${student}`]) }
}

it('loads at most four independent rows with exact request count, preserves order and within-row dependencies', async () => {
  const s = harness()
  s.setDelay(async (_kind, i) => { await new Promise(resolve => setTimeout(resolve, i === 0 ? 16 : 2)) })
  await s.login(); s.calls.length = 0
  const result = await s.client.openHomework(s.hw.id)
  expect(s.peak).toBe(4)
  expect(result.cases.map(row => row.case.id)).toEqual(s.hw.case_ids)
  expect(result.cases.map(row => row.attempt.case_id)).toEqual(s.hw.case_ids)
  expect(s.calls).toHaveLength(26)
  expect(s.calls.filter(path => /^\/v1\/(questions|attempts)\//.test(path))).toHaveLength(16)
  expect(new Set(s.calls).size).toBe(s.calls.length)
  expect(s.completed[0]).not.toBe('/v1/questions/question_0')
  for (let i = 0; i < 8; i++) {
    expect(s.calls.indexOf(`/v1/questions/question_${i}`)).toBeLessThan(s.calls.indexOf(`/v1/attempts/attempt_${i}`))
  }
})

it.each(['network', 'json', 'version'] as const)('failed %s row does not save partial detail or start later queued rows', async kind => {
  const s = harness(), release = deferred()
  s.setDelay(async (field, i) => {
    if (i > 0) await release.promise
    if (i === 0 && field === 'questions') {
      if (kind === 'network') throw new TypeError('synthetic network failure')
      if (kind === 'json') return new Response('{', { status: 200, headers: { 'Content-Type': 'application/json' } })
      return json({ record: { ...s.questions[0], revision: 2 }, current_revision: 2, currently_effective: true })
    }
  })
  await s.login()
  await expect(s.client.openHomework(s.hw.id)).rejects.toBeDefined()
  release.resolve(); await tick(); await tick()
  expect(s.peak).toBeLessThanOrEqual(4)
  expect(s.calls.filter(path => /^\/v1\/(questions|attempts)\/\w+_[4-7]$/.test(path))).toEqual([])
  expect(await s.store.drafts(s.scope())).toEqual([])
  // An explicit later GET may try again; a rejected read is not cached/retried.
  const firstGets = s.calls.filter(path => path === '/v1/questions/question_0').length
  expect(firstGets).toBe(1)
  s.setDelay(async () => undefined)
  expect((await s.client.openHomework(s.hw.id)).cases).toHaveLength(8)
  expect(s.calls.filter(path => path === '/v1/questions/question_0')).toHaveLength(2)
})

it('foreign student case is rejected before row reads or local detail writes', async () => {
  const s = harness(); s.cases[3].scope.student_id = 'student_other'
  await s.login()
  await expect(s.client.openHomework(s.hw.id)).rejects.toMatchObject({ code: 'access_denied' })
  expect(s.calls.filter(path => /^\/v1\/(questions|attempts)\//.test(path))).toEqual([])
  expect(await s.store.drafts(s.scope())).toEqual([])
})

it('logout while four questions are pending cannot read attempts or save stale detail', async () => {
  const s = harness(), release = deferred(), started = deferred(); let pending = 0
  s.setDelay(async () => { if (++pending === 4) started.resolve(); await release.promise })
  await s.login()
  const reading = s.client.openHomework(s.hw.id), rejection = expect(reading).rejects.toMatchObject({ code: 'session_changed' })
  await started.promise; await s.client.logout(); release.resolve(); await rejection; await tick()
  expect(s.client.identity).toBeNull()
  expect(s.calls.filter(path => path.startsWith('/v1/attempts/'))).toEqual([])
  expect(await s.store.drafts(s.scope())).toEqual([])
})

it('late old request 401 cannot sign out a newly logged-in student or save old rows under its scope', async () => {
  const s = harness(), release = deferred(), started = deferred(); let pending = 0
  s.setDelay(async (_field, i) => {
    if (++pending === 4) started.resolve(); await release.promise
    if (i === 0) return json({ type: 'urn:xueban:authorization', title: '合成失效', status: 401, category: 'authorization', code: 'unauthorized', detail: '合成', request_id: 'synthetic', current_revision: null, retry_after_seconds: null }, 401)
  })
  await s.login()
  const reading = s.client.openHomework(s.hw.id), rejection = expect(reading).rejects.toBeDefined()
  await started.promise; s.client.disconnect(); s.setPrincipal('two'); await s.login(); release.resolve(); await rejection; await tick()
  expect(s.client.selectedStudentId).toBe('student_two'); expect(s.client.identity?.actor_id).toBe('actor_two')
  expect(s.calls.filter(path => path.startsWith('/v1/attempts/'))).toEqual([])
  expect(await s.store.drafts(s.scope())).toEqual([]); expect(await s.store.drafts(s.scope('two'))).toEqual([])
})
