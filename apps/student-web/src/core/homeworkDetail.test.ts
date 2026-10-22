import { expect, it } from 'vitest'
import { IDBFactory } from 'fake-indexeddb'
import examples from '../../../../contracts/phase1/examples.json'
import { StudentClient } from './student'
import { DraftStore } from './store'
import { detailFromSnapshot, type HomeworkDetailRead } from './homeworkDetail'
import { valid } from './validation'
import type { Attempt, CapturedQuestionRevision, CheckRun, CorrectionCase, HomeworkSession, Job } from './types'

class MemorySession implements Storage {
  private values = new Map<string, string>()
  get length() { return this.values.size }
  clear() { this.values.clear() }
  getItem(key: string) { return this.values.get(key) ?? null }
  key(index: number) { return [...this.values.keys()][index] ?? null }
  removeItem(key: string) { this.values.delete(key) }
  setItem(key: string, value: string) { this.values.set(key, value) }
}
const example = <T>(name: string) => structuredClone(examples.entries.find(x => x.name === name)!.value) as T
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } })
const deferred = () => { let resolve!: () => void; const promise = new Promise<void>(r => { resolve = r }); return { promise, resolve } }
const problem = (status: number, code: string, category = 'conflict') => ({ type: 'urn:xueban:problem', title: '合成请求失败', status, code, category, detail: '仅测试', request_id: 'request_synthetic', current_revision: null, retry_after_seconds: null })

export function snapshot(count = 31): HomeworkDetailRead {
  const scope = { tenant_id: 'tenant_one', campus_id: 'campus_one', student_id: 'student_one' }
  const homework: HomeworkSession = { ...example<HomeworkSession>('homework'), id: 'homework_one', scope, asset_ids: ['asset_original'], question_ids: [], case_ids: [], job_ids: ['job_all'] }
  const jobs: Job[] = [{ ...example<Job>('successful_check_job'), id: 'job_all', kind: 'recognize_homework', result: { resource_type: 'homework', resource_id: homework.id } }]
  const value: HomeworkDetailRead = { protocol: 'homework-detail.v1', homework, case_records: [], questions: [], attempts: [], checks: [], jobs }
  for (let i = 0; i < count; i++) {
    const created_at = `2026-10-16T08:00:${String(i % 60).padStart(2, '0')}Z`
    const c: CorrectionCase = { ...example<CorrectionCase>('resolved_case'), id: `case_${i}`, scope: { ...scope }, homework_id: homework.id, question_id: `question_${i}`, current_question_revision: 2, root_attempt_id: `attempt_${i}`, current_attempt_id: `attempt_${i}`, progress: 'pending', current_decision: null, attempt_ids: [`attempt_${i}`], check_ids: [`check_${i}`], hint_ids: [], help_request_ids: [], review_ids: [], lifecycle: 'active', replacement_case_ids: [], created_at, updated_at: created_at }
    const q: CapturedQuestionRevision = { ...example<CapturedQuestionRevision>('question_revision_2'), question_id: c.question_id, homework_id: homework.id, label: `${i + 1}`, stem_text: `第${i + 1}题：化简 x^(2) + ${i}。`, regions: [{ asset_id: 'asset_original', x: 20, y: 40 + i * 240, width: 500, height: 220, coordinate_space: 'original_pixels' }], created_at }
    const a: Attempt = { ...example<Attempt>('original_attempt'), id: c.current_attempt_id, case_id: c.id, question_id: c.question_id, root_attempt_id: c.current_attempt_id, answer_text: `原作答 ${i + 1}`, created_at }
    const check: CheckRun = { id: `check_${i}`, revision: 1, job_id: 'job_all', case_id: c.id, input_versions: { question_id: c.question_id, question_revision: 2, attempt_id: a.id, attempt_recognition_revision: a.recognition_revision, case_revision_at_submission: c.revision, fingerprint: 'a'.repeat(64) }, model_version: null, rule_version: null, prompt_version: null, decision_ids: [], created_at }
    value.case_records.push(c); value.questions.push({ record: q, current_revision: q.revision, currently_effective: true }); value.attempts.push({ record: a, current_revision: a.revision, currently_effective: true }); value.checks.push(check)
    homework.case_ids.push(c.id); homework.question_ids.push(c.question_id)
  }
  return value
}
function harness(enabled: boolean | null = true, value = snapshot()) {
  const storage = new MemorySession(), store = new DraftStore(new IDBFactory()), calls: string[] = []
  let principal = 'one'
  let response: () => Promise<Response> = async () => json(value)
  const transport: typeof fetch = async (input, init = {}) => {
    const path = new URL(String(input)).pathname; calls.push(path)
    if (path === '/v1/auth/login') return json({ access_token: `synthetic-${principal}`, expires_at: '2099-01-01T00:00:00Z', must_change_password: false })
    if (path === '/v1/auth/account') return json({ username: `synthetic.${principal}`, student_id: `student_${principal}`, must_change_password: false })
    if (path === '/v1/auth/logout') return json({ logged_out: true })
    if (path === '/v1/me') return json({ actor_id: `actor_${principal}`, roles: ['student'], authorized_students: [{ student_id: `student_${principal}`, display_name: '合成学生', campus_id: 'campus_one' }] })
    if (init.method === 'POST') throw new Error('Unexpected business write')
    if (path === '/v1/homeworks/homework_one/detail') return response()
    if (path === '/v1/homeworks/homework_one') return json(value.homework)
    const [, kind, id] = path.match(/^\/v1\/(cases|questions|attempts|checks|jobs)\/([a-z0-9_]+)$/) ?? []
    const record = kind === 'cases' ? value.case_records.find(c => c.id === id) : kind === 'questions' ? value.questions.find(q => q.record.question_id === id) : kind === 'attempts' ? value.attempts.find(a => a.record.id === id) : kind === 'checks' ? value.checks.find(c => c.id === id) : kind === 'jobs' ? value.jobs.find(j => j.id === id) : null
    if (!record) throw new Error(`Unexpected synthetic path ${path}`)
    return json(record)
  }
  const client = new StudentClient({ origin: 'http://localhost', storage, store, transport, ...(enabled === null ? {} : { detailSnapshotEnabled: enabled }) })
  return { client, store, calls, value, storage, setResponse: (next: typeof response) => { response = next }, setPrincipal: (next: string) => { principal = next }, login: () => client.login('synthetic.student', 'synthetic password'), scope: (who = 'one') => JSON.stringify(['http://localhost', `actor_${who}`, `student_${who}`]) }
}

it('replaces exactly 127 initial GETs by identity plus snapshot, with identical full current detail', async () => {
  const legacy = harness(false), current = harness(true)
  await legacy.login(); await current.login(); legacy.calls.length = current.calls.length = 0
  const oldDetail = await legacy.client.openHomework('homework_one'), newDetail = await current.client.openHomework('homework_one')
  expect(valid('HomeworkDetailRead', current.value)).toBe(true)
  expect(legacy.calls).toHaveLength(127)
  expect(current.calls).toEqual(['/v1/me', '/v1/homeworks/homework_one/detail'])
  expect(newDetail).toEqual(oldDetail)
  expect(newDetail.cases.map(row => row.attempt.captured_question_revision)).toEqual(Array(31).fill(1)) // Original answer survives question revision 2.
  expect(newDetail.cases.every(row => row.checkJob?.id === 'job_all')).toBe(true)
})

it('defaults to the existing loader and supports explicit old-server configuration without probing a 404', async () => {
  const s = harness(null, snapshot(1)); await s.login(); s.calls.length = 0
  await s.client.openHomework('homework_one')
  expect(s.calls).not.toContain('/v1/homeworks/homework_one/detail')
  expect(s.calls).toHaveLength(7)
})

it('shuffled bundle arrays preserve original page order, bindings and raw fields without mutating the bundle', () => {
  const value = snapshot(5)
  value.questions[2].record.options = [{ label: 'B', text: ' x^(2) ' }, { label: 'A', text: '−1^2n' }]
  value.questions[3].record.stem_text = null; value.attempts[3].record.answer_text = null
  const expected = detailFromSnapshot(value, 'homework_one', 'student_one')
  for (const key of ['case_records', 'questions', 'attempts', 'checks', 'jobs'] as const) value[key].reverse()
  const before = structuredClone(value), result = detailFromSnapshot(value, 'homework_one', 'student_one')
  expect(result).toEqual(expected); expect(value).toEqual(before)
  result.cases[0].case.attempt_ids.push('attempt_private_mutation')
  expect(value).toEqual(before)
})

it('retains retired replacement records and checks only the current input among complete older check history', () => {
  const value = snapshot(3), retired = value.case_records[0]
  retired.lifecycle = 'superseded'; retired.replacement_case_ids = [value.case_records[1].id]
  value.questions.shift(); value.attempts.shift(); value.checks.shift()
  const staleCheck = structuredClone(value.checks[0]); staleCheck.id = 'check_newer_stale'; staleCheck.input_versions.attempt_recognition_revision++; staleCheck.created_at = '2026-10-16T09:00:00Z'
  const newerJob: Job = { ...value.jobs[0], id: 'job_newer_stale', kind: 'check', status: 'running', result: null }
  staleCheck.job_id = newerJob.id; value.jobs.push(newerJob); value.homework.job_ids.push(newerJob.id); value.checks.push(staleCheck); value.case_records[1].check_ids.push(staleCheck.id)
  const result = detailFromSnapshot(value, 'homework_one', 'student_one')
  expect(result.cases.map(row => row.case.id)).toEqual(['case_1', 'case_2'])
  expect(result.cases[0].checkJob?.id).toBe('job_all')
  expect(result.retired).toEqual([{ id: 'case_0', lifecycle: 'superseded', replacementCaseIds: ['case_1'] }])
})

const invalidBundles: [string, (v: HomeworkDetailRead) => void][] = [
  ['missing case', v => { v.case_records.pop() }],
  ['missing question', v => { v.questions.pop() }],
  ['missing attempt', v => { v.attempts.pop() }],
  ['missing check', v => { v.checks.pop() }],
  ['missing job', v => { v.jobs.pop() }],
  ['duplicate record', v => { v.questions.push(structuredClone(v.questions[0])) }],
  ['extra unreachable case', v => { v.case_records.push({ ...structuredClone(v.case_records[0]), id: 'case_extra' }) }],
  ['foreign tenant', v => { v.case_records[0].scope.tenant_id = 'tenant_other' }],
  ['foreign campus', v => { v.case_records[0].scope.campus_id = 'campus_other' }],
  ['foreign student', v => { v.case_records[0].scope.student_id = 'student_other' }],
  ['foreign homework question', v => { v.questions[0].record.homework_id = 'homework_other' }],
  ['foreign attempt', v => { v.attempts[0].record.case_id = 'case_other' }],
  ['foreign check', v => { v.checks[0].case_id = 'case_other' }],
  ['foreign check input', v => { v.checks[0].input_versions.attempt_id = 'attempt_other' }],
  ['unknown job reference', v => { v.checks[0].job_id = 'job_other' }],
  ['question wrapper revision', v => { v.questions[0].current_revision++ }],
  ['case question revision', v => { v.case_records[0].current_question_revision++ }],
  ['attempt wrapper revision', v => { v.attempts[0].current_revision++ }],
  ['ineffective attempt', v => { v.attempts[0].currently_effective = false }],
  ['wrong root', v => { v.attempts[0].record.root_attempt_id = 'attempt_other' }],
  ['foreign original photo', v => { v.attempts[0].record.asset_ids = ['asset_other'] }],
  ['replacement cycle', v => { v.case_records[0].lifecycle = 'superseded'; v.case_records[0].replacement_case_ids = ['case_0'] }],
  ['unknown replacement', v => { v.case_records[0].lifecycle = 'superseded'; v.case_records[0].replacement_case_ids = ['case_missing'] }],
]
it.each(invalidBundles)('rejects %s as one whole response, without falling back or writing a partial cache', async (_name, mutate) => {
  const s = harness(true, snapshot(2)); mutate(s.value); await s.login(); s.calls.length = 0
  await expect(s.client.openHomework('homework_one')).rejects.toBeDefined()
  expect(s.calls).toEqual(['/v1/me', '/v1/homeworks/homework_one/detail'])
  expect(await s.store.drafts(s.scope())).toEqual([])
})

it('only exact bounded-bundle 409 falls back to original reads', async () => {
  const s = harness(true, snapshot(2)); s.setResponse(async () => json(problem(409, 'detail_bundle_limit'), 409))
  await s.login(); s.calls.length = 0
  expect((await s.client.openHomework('homework_one')).cases).toHaveLength(2)
  expect(s.calls.filter(p => p.endsWith('/detail'))).toHaveLength(1)
  expect(s.calls).toContain('/v1/homeworks/homework_one')
})

it.each([
  [401, 'authentication_required', 'authorization'], [403, 'access_denied', 'authorization'], [404, 'not_found', 'not_found'], [409, 'stale_input', 'conflict'], [503, 'service_unavailable', 'service'],
  [409, 'detail_bundle_limit', 'authorization'], [503, 'detail_bundle_limit', 'conflict'],
] as const)('does not turn %i/%s/%s into an unsupported-server or capacity fallback', async (status, code, category) => {
  const s = harness(true, snapshot(1)); s.setResponse(async () => json(problem(status, code, category), status)); await s.login(); s.calls.length = 0
  await expect(s.client.openHomework('homework_one')).rejects.toBeDefined()
  expect(s.calls).toEqual(['/v1/me', '/v1/homeworks/homework_one/detail']); expect(await s.store.drafts(s.scope())).toEqual([])
})

it.each(['route404', 'truncated', 'network'] as const)('preserves %s failure without any old-server guess', async kind => {
  const s = harness(true, snapshot(1)); await s.login(); s.calls.length = 0
  s.setResponse(async () => { if (kind === 'network') throw new TypeError('synthetic interruption'); return kind === 'route404' ? json({ detail: 'Not Found' }, 404) : new Response('{', { status: 200 }) })
  await expect(s.client.openHomework('homework_one')).rejects.toBeDefined(); expect(s.calls).toHaveLength(2); expect(await s.store.drafts(s.scope())).toEqual([])
})

it('failed refresh preserves the complete previous cached detail instead of half an updated page', async () => {
  const s = harness(true, snapshot(2)); await s.login(); const previous = await s.client.openHomework('homework_one')
  const before = await s.store.drafts(s.scope()); s.value.questions[0].record.stem_text = '不应写进缓存的新文字'; s.value.attempts.pop()
  await expect(s.client.refreshRecognitionDraft(previous.draftId!, 'homework_one')).rejects.toBeDefined()
  expect(await s.store.drafts(s.scope())).toEqual(before)
})

it('logout while a snapshot is pending rejects the entire result and stores no row', async () => {
  const s = harness(true, snapshot(2)), started = deferred(), release = deferred()
  s.setResponse(async () => { started.resolve(); await release.promise; return json(s.value) })
  await s.login(); const pending = s.client.openHomework('homework_one'); const rejection = expect(pending).rejects.toMatchObject({ code: 'session_changed' })
  await started.promise; await s.client.logout(); release.resolve(); await rejection
  expect(await s.store.drafts(s.scope())).toEqual([])
})

it.each(['success', '401'] as const)('a late old snapshot %s cannot overwrite or revoke a new account', async kind => {
  const s = harness(true, snapshot(2)), started = deferred(), release = deferred()
  s.setResponse(async () => { started.resolve(); await release.promise; return kind === '401' ? json(problem(401, 'authentication_required', 'authorization'), 401) : json(s.value) })
  await s.login(); const pending = s.client.openHomework('homework_one'); const rejection = expect(pending).rejects.toBeDefined()
  await started.promise; s.client.disconnect(); s.setPrincipal('two'); await s.login(); release.resolve(); await rejection
  expect(s.client.selectedStudentId).toBe('student_two'); expect(s.client.identity?.actor_id).toBe('actor_two')
  expect(await s.store.drafts(s.scope())).toEqual([]); expect(await s.store.drafts(s.scope('two'))).toEqual([])
})

it('same-token restore changes the session object and prevents an older identity response from publishing old rows', async () => {
  const s = harness(true, snapshot(1)), started = deferred(), release = deferred()
  s.setResponse(async () => { started.resolve(); await release.promise; return json(s.value) })
  await s.login(); const pending = s.client.openHomework('homework_one'); const rejection = expect(pending).rejects.toBeDefined()
  await started.promise; await s.client.restore(); release.resolve(); await rejection
  expect(s.client.selectedStudentId).toBe('student_one'); expect(await s.store.drafts(s.scope())).toEqual([])
})

it.each(['drafts', 'hasPending'] as const)('same-token restore during the %s storage await cannot save an older snapshot', async stage => {
  const s = harness(true, snapshot(2)), started = deferred(), release = deferred()
  await s.login()
  if (stage === 'drafts') {
    const original = s.store.drafts.bind(s.store)
    s.store.drafts = async (...args) => { started.resolve(); await release.promise; return original(...args) }
  } else {
    const original = s.store.hasPending.bind(s.store)
    s.store.hasPending = async (...args) => { started.resolve(); await release.promise; return original(...args) }
  }
  const pending = s.client.openHomework('homework_one'), rejection = expect(pending).rejects.toMatchObject({ code: 'session_changed' })
  await started.promise; await s.client.restore(); release.resolve(); await rejection
  expect(await s.store.drafts(s.scope())).toEqual([])
})

it('does not turn a capacity response from a previous restored session into new legacy reads', async () => {
  const s = harness(true, snapshot(1)), started = deferred(), release = deferred()
  s.setResponse(async () => { started.resolve(); await release.promise; return json(problem(409, 'detail_bundle_limit'), 409) })
  await s.login(); s.calls.length = 0
  const pending = s.client.openHomework('homework_one'), rejection = expect(pending).rejects.toMatchObject({ code: 'session_changed' })
  await started.promise; await s.client.restore(); release.resolve(); await rejection
  expect(s.calls).not.toContain('/v1/homeworks/homework_one')
  expect(await s.store.drafts(s.scope())).toEqual([])
})

it('never stores a partial oversized JSON response or retries using the old loader', async () => {
  const s = harness(true, snapshot(1)); await s.login(); s.calls.length = 0
  s.setResponse(async () => new Response('{"large":"' + 'x'.repeat(2_000_001), { status: 200 }))
  await expect(s.client.openHomework('homework_one')).rejects.toMatchObject({ code: 'invalid_response' })
  expect(s.calls).toHaveLength(2); expect(await s.store.drafts(s.scope())).toEqual([])
})

it('does not accept a foreign embedded current decision in an otherwise valid bundle', () => {
  const value = snapshot(1), decision = example<CorrectionCase>('resolved_case').current_decision!
  decision.case_id = 'case_other'; decision.input_versions.question_id = 'question_0'; decision.input_versions.attempt_id = 'attempt_0'
  value.case_records[0].current_decision = decision
  expect(() => detailFromSnapshot(value, 'homework_one', 'student_one')).toThrow()
})

it('handles shared replacement DAGs without losing a longer route or duplicating a merged active case', () => {
  const value = snapshot(4)
  for (const i of [0, 1, 2]) {
    value.case_records[i].lifecycle = 'superseded'
    value.case_records[i].replacement_case_ids = i === 0 ? ['case_2', 'case_1'] : i === 1 ? ['case_2'] : ['case_3']
  }
  value.questions.splice(0, 3); value.attempts.splice(0, 3); value.checks.splice(0, 3)
  const result = detailFromSnapshot(value, 'homework_one', 'student_one')
  expect(result.cases.map(row => row.case.id)).toEqual(['case_3'])
  expect(result.retired).toHaveLength(3)
  const tooDeep = snapshot(10)
  for (let i = 0; i < 9; i++) { tooDeep.case_records[i].lifecycle = 'superseded'; tooDeep.case_records[i].replacement_case_ids = [`case_${i + 1}`] }
  tooDeep.case_records[0].replacement_case_ids.unshift('case_8')
  tooDeep.questions.splice(0, 9); tooDeep.attempts.splice(0, 9); tooDeep.checks.splice(0, 9)
  expect(() => detailFromSnapshot(tooDeep, 'homework_one', 'student_one')).toThrow()
})

it.each([false, true])('never saves a truncated 301-case homework in snapshot mode %s', async enabled => {
  const s = harness(enabled, snapshot(301))
  if (enabled) s.setResponse(async () => json(problem(409, 'detail_bundle_limit'), 409))
  await s.login(); s.calls.length = 0
  await expect(s.client.openHomework('homework_one')).rejects.toMatchObject({ code: 'detail_bundle_limit' })
  expect(s.calls.filter(p => p.startsWith('/v1/cases/'))).toHaveLength(300)
  expect(s.calls.some(p => p.startsWith('/v1/questions/'))).toBe(false)
  expect(await s.store.drafts(s.scope())).toEqual([])
})

it('rejects replacement expansion past the legacy capacity without replacing a complete cached page', async () => {
  const s = harness(true, snapshot(1)); await s.login()
  await s.client.openHomework('homework_one'); const before = await s.store.drafts(s.scope())
  Object.assign(s.value, snapshot(301))
  s.value.homework.case_ids = ['case_0']
  s.value.case_records[0].lifecycle = 'superseded'
  s.value.case_records[0].replacement_case_ids = s.value.case_records.slice(1).map(c => c.id)
  s.setResponse(async () => json(problem(409, 'detail_bundle_limit'), 409)); s.calls.length = 0
  await expect(s.client.openHomework('homework_one')).rejects.toMatchObject({ code: 'detail_bundle_limit' })
  expect(s.calls.filter(p => p.startsWith('/v1/cases/'))).toHaveLength(300)
  expect(await s.store.drafts(s.scope())).toEqual(before)
})

it('keeps all 300 reachable records and permits shared replacements already read within the limit', async () => {
  const value = snapshot(300)
  value.case_records[0].lifecycle = 'superseded'; value.case_records[0].replacement_case_ids = ['case_1', 'case_2']
  value.case_records[1].lifecycle = 'superseded'; value.case_records[1].replacement_case_ids = ['case_2']
  const s = harness(true, value); s.setResponse(async () => json(problem(409, 'detail_bundle_limit'), 409)); await s.login(); s.calls.length = 0
  const result = await s.client.openHomework('homework_one')
  expect(s.calls.filter(p => p.startsWith('/v1/cases/'))).toHaveLength(300)
  expect(result.cases).toHaveLength(298)
  expect(result.retired).toHaveLength(2)
  expect(result.cases.filter(row => row.case.id === 'case_2')).toHaveLength(1)
})
