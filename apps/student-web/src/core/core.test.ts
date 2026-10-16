import { describe, expect, it, vi } from 'vitest'
import { IDBFactory } from 'fake-indexeddb'
import { StudentClient, decisionLabel } from './student'
import { DraftStore } from './store'
import { StudentApi } from './api'
import { imageDimensions } from './photo'
import { valid } from './validation'
import examples from '../../../../contracts/phase1/examples.json'
import type { Attempt, CapturedQuestionRevision, CaseDetail, CorrectionCase, Decision, Hint, HomeworkSession, ImageAsset, Job, NormalizedPhoto } from './types'

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
const now = '2026-10-17T00:00:00Z'
const photo: NormalizedPhoto = { blob: new Blob(['synthetic-image'], { type: 'image/jpeg' }), width: 20, height: 20, sha256: 'a'.repeat(64), mime: 'image/jpeg', size: 15 }
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } })
function setup() {
  const storage = new MemorySession(), factory = new IDBFactory(), store = new DraftStore(factory)
  const identity = { actor_id: 'actor_test', roles: ['student'], authorized_students: [{ student_id: 'student_one', display_name: '合成学生一', campus_id: 'campus_one' }, { student_id: 'student_two', display_name: '合成学生二', campus_id: 'campus_one' }] }
  const hw: HomeworkSession = { ...fixture<HomeworkSession>('homework'), id: 'homework_one', revision: 1, scope: { tenant_id: 'tenant_one', campus_id: 'campus_one', student_id: 'student_one' }, asset_ids: [], question_ids: [], case_ids: [], job_ids: [] }
  const asset: ImageAsset = { ...fixture<ImageAsset>('asset'), id: 'asset_one', homework_id: hw.id, status: 'awaiting_upload', revision: 1, width: 20, height: 20, sha256: photo.sha256, byte_size: photo.size, mime_type: photo.mime }
  const job: Job = { id: 'job_recognition', revision: 1, kind: 'recognize_homework', status: 'succeeded', created_at: now, updated_at: now, retry_after_seconds: null, failure: null, result: { resource_type: 'homework', resource_id: hw.id } }
  const calls: { path: string; init: RequestInit }[] = []
  let loseLogin = false, loseRecognition = false, revoke = false, imageTicket = 0, conflictCode: string | null = null
  let extra: ((path: string, init: RequestInit) => Response | undefined) | undefined
  const transport = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input)), path = url.pathname; calls.push({ path, init: init ?? {} })
    if (path === '/v1/auth/login') { if (loseLogin) { loseLogin = false; throw new TypeError('synthetic lost response') } return json({ access_token: 'synthetic.access.token', expires_at: '2099-01-01T00:00:00Z', must_change_password: false }) }
    if (path === '/v1/auth/account') return json({ username: 'synthetic.student', student_id: 'student_one', must_change_password: false })
    if (path === '/v1/me') return revoke ? json({}, 403) : json(identity)
    const extraResponse = extra?.(path, init ?? {}); if (extraResponse) return extraResponse
    if (path === '/v1/students/student_one/homeworks' && init?.method === 'POST') return json(hw, 201)
    if (path === `/v1/homeworks/${hw.id}`) return json(hw)
    if (path === `/v1/homeworks/${hw.id}/uploads`) {
      if (conflictCode) { const code = conflictCode; conflictCode = null; hw.revision++; return json({ type: 'urn:xueban:conflict', title: '合成冲突', status: 409, category: 'conflict', code, detail: '合成拒绝', request_id: 'request_test', current_revision: hw.revision, retry_after_seconds: null }, 409) }
      hw.asset_ids = [asset.id]; hw.revision++; return json({ asset, put_url: `http://localhost/v1/assets/${asset.id}/content`, expires_at: '2099-01-01T00:00:00Z', required_headers: { 'X-Upload-Token': 'synthetic-ticket', 'Content-Type': 'image/jpeg' } }) }
    if (path === `/v1/assets/${asset.id}/upload-ticket`) return json({ asset, put_url: `http://localhost/v1/assets/${asset.id}/content`, expires_at: '2099-01-01T00:00:00Z', required_headers: { 'X-Upload-Token': 'synthetic-ticket', 'Content-Type': 'image/jpeg' } })
    if (path === `/v1/assets/${asset.id}`) return json(asset)
    if (path === `/v1/assets/${asset.id}/content`) return init?.method === 'PUT' ? new Response(null, { status: 204 }) : imageTicket === 1 ? json({}, 401) : new Response('synthetic-image', { headers: { 'Content-Type': 'image/jpeg' } })
    if (path === `/v1/assets/${asset.id}/access`) { imageTicket++; return json({ asset_id: asset.id, read_url: `http://localhost/v1/assets/${asset.id}/content?ticket=${imageTicket}`, expires_at: '2099-01-01T00:00:00Z' }) }
    if (path === `/v1/assets/${asset.id}/complete`) { asset.status = 'uploaded'; asset.revision++; return json(asset) }
    if (path === `/v1/homeworks/${hw.id}/recognitions`) { hw.job_ids = [job.id]; hw.revision++; if (loseRecognition) { loseRecognition = false; throw new TypeError('synthetic lost recognition response') } return json({ job, created_resource: null }, 202) }
    if (path === `/v1/jobs/${job.id}`) return json(job)
    throw new Error(`Unexpected synthetic route ${path}`)
  }) as unknown as typeof fetch
  const options = { origin: 'http://localhost', storage, store, transport, normalize: async () => photo, sleep: async () => undefined }
  return { options, client: new StudentClient(options), storage, store, identity, hw, job, calls, loseLogin: () => { loseLogin = true }, loseRecognition: () => { loseRecognition = true }, revoke: () => { revoke = true }, conflict: (code: string) => { conflictCode = code }, route: (handler: typeof extra) => { extra = handler } }
}

describe('student session and durable corrections', () => {
  it('retries uncertain login with the same in-memory key without storing credentials', async () => {
    const s = setup(); s.loseLogin()
    await expect(s.client.login('synthetic.student', 'synthetic password only')).rejects.toMatchObject({ code: 'network_unknown' })
    expect(s.storage.length).toBe(0)
    await s.client.login('synthetic.student', 'synthetic password only')
    const posts = s.calls.filter(c => c.path === '/v1/auth/login')
    expect(posts).toHaveLength(2); expect(posts[0].init.body).toBe(posts[1].init.body); expect(posts[0].init.headers).toEqual(posts[1].init.headers)
    expect(s.storage.getItem('xueban:http://localhost:session')).not.toContain('synthetic password')
    expect(s.storage.getItem('xueban:http://localhost:session')).not.toContain('synthetic.student')
    const next = new StudentClient(s.options); await next.restore(); expect(next.selectedStudentId).toBe('student_one')
  })
  it('does not notify on unchanged authorization reads, including a subscriber that reads drafts', async () => {
    const s = setup(); await s.client.login('synthetic.student', 'synthetic password only'); let reads = 0
    const unsubscribe = s.client.subscribe(() => { reads++; if (reads < 5) void s.client.listDrafts() })
    await s.client.createDraft(photo.blob); await new Promise(resolve => setTimeout(resolve, 30)); unsubscribe()
    expect(reads).toBe(1)
    expect(s.calls.filter(c => c.path === '/v1/me').length).toBeLessThan(6)
  })
  it('isolates draft and blobs by actor and student, and clears authorization on a revoked me', async () => {
    const s = setup(); await s.client.login('synthetic.student', 'synthetic password only'); const d = await s.client.createDraft(photo.blob)
    expect(() => s.client.selectStudent('student_two')).toThrow();
    s.client.selectStudent('student_one'); expect((await s.client.getDraft(d.id)).photo).toBeInstanceOf(Blob)
    s.identity.actor_id = 'actor_other'; const other = new StudentClient(s.options); await other.restore(); expect(await other.listDrafts()).toEqual([])
    s.revoke(); await expect(s.client.listDrafts()).rejects.toMatchObject({ status: 403 }); expect(s.client.identity).toBeNull(); expect(s.client.selectedStudentId).toBeNull()
  })
  it('revoked student grants hide cached data even when other students stay authorized', async () => {
    const s = setup(); await s.client.login('synthetic.student', 'synthetic password only'); const d = await s.client.createDraft(photo.blob)
    s.identity.authorized_students.shift(); await expect(s.client.getDraft(d.id)).rejects.toMatchObject({ status: 403 }); expect(s.client.identity).toBeNull()
  })
  it('normalizes/imports locally and starts no homework or model job until explicitly run', async () => {
    const s = setup(); await s.client.login('synthetic.student', 'synthetic password only'); const d = await s.client.createDraft(photo.blob)
    expect(d.stage).toBe('local'); expect(s.calls.filter(c => c.init.method === 'POST')).toHaveLength(1)
    const result = await s.client.runDraft(d.id)
    expect(result.stage).toBe('complete'); expect(result.detail?.cases).toEqual([]); expect(result.detail?.homework.coverage.collection_scope).not.toBe('declared_complete')
    const put = s.calls.find(c => c.init.method === 'PUT')!
    expect(put.init.headers).not.toHaveProperty('Authorization'); expect(put.init.credentials).toBe('omit'); expect(put.init.redirect).toBe('error')
  })
  it('history opening preserves the original draft and unknown recognition journal across reload', async () => {
    const s = setup(); await s.client.login('synthetic.student', 'synthetic password only'); const d = await s.client.createDraft(photo.blob)
    s.loseRecognition(); const failed = await s.client.runDraft(d.id); expect(failed.stage).toBe('failed')
    const restored = new StudentClient(s.options); await restored.restore()
    const detail = await restored.openHomework(s.hw.id); expect(detail.draftId).toBe(d.id)
    const result = await restored.runDraft(detail.draftId!, { retryFailed: true }); expect(result.stage).toBe('complete')
    const writes = s.calls.filter(c => c.path.endsWith('/recognitions'))
    expect(writes).toHaveLength(2); expect(writes[0].init.body).toBe(writes[1].init.body); expect(writes[0].init.headers).toEqual(writes[1].init.headers)
    expect((await restored.listDrafts()).map(d => d.id)).toEqual([d.id])
  })
  it('uses one asset single-flight and retries a replaced ticket without revoking JWT', async () => {
    const s = setup(); await s.client.login('synthetic.student', 'synthetic password only')
    const [one, two] = await Promise.all([s.client.assetBlob('asset_one'), s.client.assetBlob('asset_one')])
    expect(one).toBe(two); expect(s.calls.filter(c => c.path.endsWith('/access'))).toHaveLength(2)
    expect(s.client.identity).not.toBeNull(); await s.client.assetBlob('asset_one'); expect(s.calls.filter(c => c.path.endsWith('/access'))).toHaveLength(2)
  })
  it.each(['revision_conflict', 'idempotency_conflict'])('only rotates a known unaccepted CAS conflict, not %s arbitrarily', async code => {
    const s = setup(); await s.client.login('synthetic.student', 'synthetic password only'); const d = await s.client.createDraft(photo.blob)
    s.conflict(code); expect((await s.client.runDraft(d.id)).error?.code).toBe(code)
    await s.client.runDraft(d.id, { retryFailed: true })
    const uploads = s.calls.filter(c => c.path.endsWith('/uploads'))
    expect(uploads).toHaveLength(2)
    const keys = uploads.map(c => (c.init.headers as Record<string, string>)['Idempotency-Key'])
    if (code === 'revision_conflict') { expect(keys[0]).not.toBe(keys[1]); expect(uploads[0].init.body).not.toBe(uploads[1].init.body) }
    else { expect(keys[0]).toBe(keys[1]); expect(uploads[0].init.body).toBe(uploads[1].init.body) }
  })
})

function correctionSetup() {
  const s = setup(), c = fixture<CorrectionCase>('resolved_case'), q = fixture<CapturedQuestionRevision>('question_revision_2')
  let a = fixture<Attempt>('correction_across_question_revision')
  c.scope = s.hw.scope; c.homework_id = s.hw.id; c.current_decision = null; c.progress = 'editing'; q.homework_id = s.hw.id
  s.hw.case_ids = [c.id]; s.hw.question_ids = [q.question_id]
  const jobs = new Map<string, Job>(); let count = 0
  s.route((path, init) => {
    if (path === `/v1/cases/${c.id}`) return json(c)
    if (path === `/v1/questions/${q.question_id}`) return json({ record: q, current_revision: q.revision, currently_effective: true })
    if (path === `/v1/attempts/${a.id}`) return json({ record: a, current_revision: a.revision, currently_effective: true })
    if (path === `/v1/cases/${c.id}/attempts`) {
      const payload = JSON.parse(init.body as string); expect(payload.parent_attempt_id).toBe(a.id); count++
      a = { ...a, id: `attempt_retry${count}`, parent_attempt_id: a.id, recognition_status: count === 1 ? 'failed' : 'needs_revision', answer_text: null, answer_regions: [] }
      c.current_attempt_id = a.id; c.attempt_ids.push(a.id); c.revision++
      const job: Job = { ...s.job, id: `job_correction${count}`, kind: 'recognize_attempt', status: count === 1 ? 'failed' : 'succeeded', failure: count === 1 ? { code: 'invalid_provider_output', message: '合成失败', retryable: false } : null, result: count === 1 ? null : { resource_type: 'attempt', resource_id: a.id } }
      jobs.set(job.id, job); s.hw.job_ids.push(job.id)
      return json({ job, created_resource: { resource_type: 'attempt', resource_id: a.id } })
    }
    const job = jobs.get(path.split('/').at(-1)!); if (path.startsWith('/v1/jobs/') && job) return json(job)
  })
  return { ...s, c, q, current: () => a, replaceAttempt: () => { a.id = 'attempt_newer'; c.current_attempt_id = a.id } }
}

it('explicit retry of failed correction recognition chains to that failed attempt without overwriting a newer one', async () => {
  const s = correctionSetup(); await s.client.login('synthetic.student', 'synthetic password only'); const d = await s.client.createCorrection(s.c.id, photo.blob)
  expect(d.correction?.parentAttemptId).toBe('attempt_correction')
  const failed = await s.client.runDraft(d.id); expect(failed.stage).toBe('failed')
  const result = await s.client.runDraft(d.id, { retryFailed: true }); expect(result.stage).toBe('complete'); expect(s.current().parent_attempt_id).toBe('attempt_retry1')
  expect(result.detail?.cases[0].attempt.recognition_status).toBe('needs_revision')
})

it('correction retry and replacing its preview refuse a newer parent from another device', async () => {
  const s = correctionSetup(); await s.client.login('synthetic.student', 'synthetic password only'); const d = await s.client.createCorrection(s.c.id, photo.blob)
  await s.client.runDraft(d.id); s.replaceAttempt()
  const result = await s.client.runDraft(d.id, { retryFailed: true }); expect(result.error?.code).toBe('newer_correction_exists')
  await expect(s.client.createCorrection(s.c.id, photo.blob, 'unknown', d.correction!.parentAttemptId)).rejects.toMatchObject({ code: 'newer_correction_exists' })
  expect(s.calls.filter(c => c.path.endsWith('/attempts'))).toHaveLength(1)
})

it('reconciles a pending recognition even when remote cases already exist', async () => {
  const s = correctionSetup(); s.hw.case_ids = []; await s.client.login('synthetic.student', 'synthetic password only'); const d = await s.client.createDraft(photo.blob)
  s.loseRecognition(); await s.client.runDraft(d.id)
  s.hw.case_ids = [s.c.id]; s.current().recognition_status = 'needs_revision'
  const client = new StudentClient(s.options); await client.restore(); const detail = await client.openHomework(s.hw.id)
  await client.runDraft(detail.draftId!)
  const requests = s.calls.filter(c => c.path.endsWith('/recognitions')); expect(requests).toHaveLength(2); expect(requests[0].init.headers).toEqual(requests[1].init.headers)
  const scope = JSON.stringify(['http://localhost', 'actor_test', 'student_one']); expect(await s.store.hasPending(scope, `${d.id}:`)).toBe(false)
})

it('preserves unknown creation key across a later authorization denial and regrant', async () => {
  const s = setup(), original = s.options.transport, writes: RequestInit[] = []
  const transport: typeof fetch = async (input, init) => {
    if (String(input).endsWith('/students/student_one/homeworks') && init?.method === 'POST') {
      writes.push(init)
      if (writes.length === 1) { await original(input, init); throw new TypeError('synthetic lost commit response') }
      if (writes.length === 2) return json({}, 403)
    }
    return original(input, init)
  }
  const client = new StudentClient({ ...s.options, transport }); await client.login('synthetic.student', 'synthetic password only'); const d = await client.createDraft(photo.blob)
  expect((await client.runDraft(d.id)).stage).toBe('failed')
  expect((await client.runDraft(d.id, { retryFailed: true })).error?.code).toBe('http_403')
  expect((await client.runDraft(d.id, { retryFailed: true })).stage).toBe('complete')
  expect(writes).toHaveLength(3); expect(writes.map(x => x.body)).toEqual([writes[0].body, writes[0].body, writes[0].body]); expect(writes.map(x => x.headers)).toEqual([writes[0].headers, writes[0].headers, writes[0].headers])
})

it('invokes default browser fetch with its global receiver, never as an API member', async () => {
  const original = globalThis.fetch
  globalThis.fetch = async function (this: unknown) { expect(this).toBe(globalThis); return json({ actor_id: 'actor_test', roles: ['student'], authorized_students: [] }) }
  try { const api = new StudentApi('http://localhost', () => 'synthetic-token', () => undefined); await expect(api.request('/v1/me', 'IdentityContext')).resolves.toMatchObject({ actor_id: 'actor_test' }) }
  finally { globalThis.fetch = original }
})

it('allows an explicit second hint request after known terminal failure with a new key, without auto-retrying', async () => {
  const s = setup(), c = fixture<CorrectionCase>('resolved_case'), q = fixture<CapturedQuestionRevision>('question_revision_2'), a = fixture<Attempt>('correction_across_question_revision'), d = fixture<Decision>('model_candidate'), hint = fixture<Hint>('shown_but_invalidated_hint')
  c.scope = s.hw.scope; c.homework_id = s.hw.id; q.homework_id = s.hw.id
  d.input_versions.question_revision = c.current_question_revision; d.input_versions.attempt_id = a.id; d.input_versions.attempt_recognition_revision = a.recognition_revision; c.current_decision = d; c.progress = 'needs_correction'
  Object.assign(hint, { id: 'hint_test', case_id: c.id, attempt_id: a.id, source_decision_id: d.id, source_decision_revision: d.revision, validity: 'active', first_shown_at: null })
  const jobs = new Map<string, Job>(); let count = 0
  s.route((path, init) => {
    if (path === `/v1/cases/${c.id}`) return json(c)
    if (path === `/v1/questions/${q.question_id}`) return json({ record: q, current_revision: q.revision, currently_effective: true })
    if (path === `/v1/attempts/${a.id}`) return json({ record: a, current_revision: a.revision, currently_effective: true })
    if (path === `/v1/cases/${c.id}/hints` && init.method === 'POST') { count++; const job: Job = { ...s.job, id: `job_hint${count}`, kind: 'hint', status: count === 1 ? 'failed' : 'succeeded', failure: count === 1 ? { code: 'invalid_provider_output', message: '合成失败', retryable: false } : null, result: count === 1 ? null : { resource_type: 'hint', resource_id: hint.id } }; jobs.set(job.id, job); return json({ job, created_resource: null }) }
    if (path === `/v1/hints/${hint.id}`) return json(hint)
    if (path.startsWith('/v1/jobs/')) return json(jobs.get(path.split('/').at(-1)!))
  })
  await s.client.login('synthetic.student', 'synthetic password only'); await expect(s.client.requestHint(c.id, 'direction')).rejects.toMatchObject({ code: 'job_failed' }); expect(count).toBe(1)
  await expect(s.client.requestHint(c.id, 'direction')).resolves.toMatchObject({ id: hint.id }); expect(count).toBe(2)
  const posts = s.calls.filter(c => c.path.endsWith('/hints')); expect(posts[0].init.headers).not.toEqual(posts[1].init.headers)
})

it('reserves a journal entry atomically and never replaces pending body/key', async () => {
  const store = new DraftStore(new IDBFactory()), base = { scope: 'scope_a', id: 'op', path: '/v1/test', key: 'key_first', body: '{"revision":1}', shape: 'Accepted' }
  const [a, b] = await Promise.all([store.reserve(base), store.reserve({ ...base, key: 'key_second', body: '{"revision":2}' })])
  expect(a).toEqual(b); expect(a.key).toBe('key_first'); expect(await store.operation('scope_b', 'op')).toBeUndefined()
})

it('rejects foreign storage URLs and bearer/cookie headers before fetching', async () => {
  const transport = vi.fn() as unknown as typeof fetch, api = new StudentApi('http://localhost', () => 'synthetic-token', () => undefined, transport)
  await expect(api.upload('https://foreign.invalid/v1/assets/asset_x/content', {}, photo.blob)).rejects.toMatchObject({ code: 'unsafe_asset_url' })
  await expect(api.upload('http://localhost/v1/assets/asset_x/content', { Authorization: 'Bearer secret' }, photo.blob)).rejects.toMatchObject({ code: 'unsafe_upload_header' })
  expect(transport).not.toHaveBeenCalled()
})

it('standalone validation works when dynamic code construction is blocked', () => {
  const original = globalThis.Function
  try { globalThis.Function = (() => { throw new Error('CSP blocked eval') }) as unknown as FunctionConstructor
    expect(valid('IdentityContext', { actor_id: 'actor_test', roles: ['student'], authorized_students: [] })).toBe(true)
    expect(valid('IdentityContext', { actor_id: 'actor_test', roles: ['student'], authorized_students: [], extra: true })).toBe(false)
  } finally { globalThis.Function = original }
})

it('preserves candidate/needs-review boundaries even if the model verdict says correct', () => {
  const row: CaseDetail = { case: fixture('resolved_case'), question: fixture('question_revision_2'), attempt: fixture('correction_across_question_revision') }
  const d = row.case.current_decision!; d.validity = 'needs_review'; d.origin = 'model'; d.input_versions.attempt_id = row.case.current_attempt_id; d.input_versions.question_revision = row.case.current_question_revision; d.input_versions.attempt_recognition_revision = row.attempt.recognition_revision
  expect(decisionLabel(row)).toContain('看不准')
})

it('rejects oversized decoded images and unsupported files before bitmap decoding', () => {
  const bytes = new Uint8Array(32); bytes.set([137, 80, 78, 71], 0); bytes.set([73, 72, 68, 82], 12); const view = new DataView(bytes.buffer); view.setUint32(16, 8000); view.setUint32(20, 8000)
  expect(() => imageDimensions(bytes)).toThrow(); view.setUint32(16, 1080); view.setUint32(20, 1920); expect(imageDimensions(bytes)).toEqual({ width: 1080, height: 1920 })
  expect(() => imageDimensions(new TextEncoder().encode('<svg>unsupported</svg>'))).toThrow()
})
