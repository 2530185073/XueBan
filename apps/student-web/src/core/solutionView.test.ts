import { describe, expect, it, vi } from 'vitest'
import { IDBFactory } from 'fake-indexeddb'
import { StudentClient } from './student'
import { DraftStore } from './store'
import examples from '../../../../contracts/phase1/examples.json'
import type { CaseDetail, Decision } from './types'
const fixture = <T,>(name: string) => structuredClone(examples.entries.find(value => value.name === name)!.value) as T
const answer = (): CaseDetail => {
  const decision = fixture<Decision>('model_candidate')
  Object.assign(decision, { id: 'decision_one', case_id: 'case_one', verdict: 'incorrect' })
  Object.assign(decision.input_versions, { question_id: 'question_one', question_revision: 1, attempt_id: 'attempt_one' })
  const row = { case: fixture<CaseDetail['case']>('resolved_case'), question: fixture<CaseDetail['question']>('question_revision_1'), attempt: fixture<CaseDetail['attempt']>('original_attempt') }
  Object.assign(row.case, { id: 'case_one', question_id: 'question_one', current_attempt_id: 'attempt_one', root_attempt_id: 'attempt_one', current_question_revision: 1, current_decision: decision, progress: 'needs_correction', attempt_ids: ['attempt_one'] })
  row.case.scope.student_id = 'student_one'
  Object.assign(row.question, { question_id: 'question_one' })
  Object.assign(row.attempt, { id: 'attempt_one', case_id: 'case_one', question_id: 'question_one', root_attempt_id: 'attempt_one' })
  return row
}
async function setup() {
  const values = new Map<string, string>(), storage = { get length() { return values.size }, clear: () => values.clear(), getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => { values.set(key, value) }, removeItem: (key: string) => { values.delete(key) }, key: (index: number) => [...values.keys()][index] ?? null }
  const row = answer(), posts: RequestInit[] = []
  let fail = false, mismatch = false, onPost: (() => void) | undefined
  const transport = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const path = new URL(String(input)).pathname, json = (value: unknown) => new Response(JSON.stringify(value))
    if (path === '/v1/auth/login') return json({ access_token: 'synthetic.token', expires_at: '2099-01-01T00:00:00Z', must_change_password: false })
    if (path === '/v1/auth/account') return json({ username: 'student.one', student_id: 'student_one', must_change_password: false })
    if (path === '/v1/me') return json({ actor_id: 'actor_one', roles: ['student'], authorized_students: [{ student_id: 'student_one', display_name: '合成学生', campus_id: 'campus_one' }] })
    if (path === '/v1/cases/case_one') return json(row.case)
    if (path === '/v1/questions/question_one') return json({ record: row.question, current_revision: row.question.revision, currently_effective: true })
    if (path === '/v1/attempts/attempt_one') return json({ record: row.attempt, current_revision: row.attempt.revision, currently_effective: true })
    if (path === '/v1/cases/case_one/solution-view') { posts.push(init); onPost?.(); if (fail) { fail = false; throw new Error('lost response') }; return json({ ...JSON.parse(String(init.body)), case_id: mismatch ? 'case_other' : 'case_one', exposed: true }) }
    throw new Error(`Unexpected ${path}`)
  }) as unknown as typeof fetch
  const options = { origin: 'http://localhost', storage, store: new DraftStore(new IDBFactory()), transport }
  const client = new StudentClient(options); await client.login('student.one', 'synthetic password')
  const read = vi.spyOn(client as unknown as { caseDetail: (...args: unknown[]) => Promise<CaseDetail> }, 'caseDetail').mockImplementation(async () => structuredClone(row))
  return { client, row, read, posts, options, lose: () => { fail = true }, mismatch: () => { mismatch = true }, afterPost: (callback: () => void) => { onPost = callback } }
}
describe('persisted solution exposure', () => {
  it('sends the current answer and decision binding before allowing exposure', async () => {
    const s = await setup(); await s.client.recordSolutionViewed(s.row)
    expect(JSON.parse(String(s.posts[0].body))).toEqual({ attempt_id: 'attempt_one', source_decision_id: 'decision_one', source_decision_revision: 1 })
    expect(s.read).toHaveBeenCalledTimes(2)
  })
  it('reuses the idempotency key after a lost response and a browser-style client restore', async () => {
    const s = await setup(); s.lose(); await expect(s.client.recordSolutionViewed(s.row)).rejects.toMatchObject({ code: 'network_unknown' })
    const next = new StudentClient(s.options); await next.restore()
    vi.spyOn(next as unknown as { caseDetail: (...args: unknown[]) => Promise<CaseDetail> }, 'caseDetail').mockResolvedValue(s.row)
    await next.recordSolutionViewed(s.row)
    expect(s.posts[0].headers).toEqual(s.posts[1].headers); expect(s.posts[0].body).toEqual(s.posts[1].body)
  })
  it('rejects changed answers without recording or showing the old solution', async () => {
    const s = await setup(), old = structuredClone(s.row); s.row.attempt.id = 'attempt_new'
    await expect(s.client.recordSolutionViewed(old)).rejects.toMatchObject({ code: 'stale_source' }); expect(s.posts).toHaveLength(0)
  })
  it('rejects a mismatched server receipt', async () => {
    const s = await setup(); s.mismatch(); await expect(s.client.recordSolutionViewed(s.row)).rejects.toMatchObject({ code: 'invalid_response' })
  })
  it('does not reveal a saved solution when the answer changes before the receipt is checked', async () => {
    const s = await setup(), newer = structuredClone(s.row)
    newer.attempt.id = 'attempt_new'
    s.read.mockResolvedValueOnce(s.row).mockResolvedValueOnce(newer)
    await expect(s.client.recordSolutionViewed(s.row)).rejects.toMatchObject({ code: 'stale_source' })
    expect(s.posts).toHaveLength(1)
  })
  it.each(['rechecking', 'editing'] as const)('blocks %s from real single-case HTTP reads without a checkJob field', async progress => {
    const s = await setup()
    s.read.mockRestore(); s.row.case.progress = progress
    await expect(s.client.recordSolutionViewed(s.row)).rejects.toMatchObject({ code: 'stale_source' })
    expect(s.posts).toHaveLength(0)
  })
  it('keeps the solution hidden if a new check starts after the write', async () => {
    const s = await setup(); s.read.mockRestore()
    s.afterPost(() => { s.row.case.progress = 'rechecking' })
    await expect(s.client.recordSolutionViewed(s.row)).rejects.toMatchObject({ code: 'stale_source' })
    expect(s.posts).toHaveLength(1)
  })
  it('reveals using normal case reads without an optional recovery capability', async () => {
    const s = await setup(); s.read.mockRestore()
    await s.client.recordSolutionViewed(s.row)
    expect(s.posts).toHaveLength(1)
  })
})
