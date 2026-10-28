import { describe, expect, it, vi } from 'vitest'
import { IDBFactory } from 'fake-indexeddb'
import { StudentClient, decisionLabel, effectiveDecision, NO_PROGRESS_TIMEOUT_MS } from './student'
import { DraftStore } from './store'
import { StudentApi } from './api'
import { imageDimensions } from './photo'
import { valid } from './validation'
import examples from '../../../../contracts/phase1/examples.json'
import type { Attempt, CapturedQuestionRevision, CaseDetail, CheckRun, CorrectionCase, Decision, DraftView, Hint, HomeworkSession, ImageAsset, Job, NormalizedPhoto, StoredDraft } from './types'

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

/** Simulates a completed page job that atomically persisted all per-question results. */
function wholePageSetup(states: (Pick<Decision, 'validity' | 'verdict'> | null)[]) {
  const s = setup()
  const rows: CaseDetail[] = states.map((state, index) => {
    const suffix = String(index + 1), caseId = `case_page${suffix}`, questionId = `question_page${suffix}`, attemptId = `attempt_page${suffix}`
    const q: CapturedQuestionRevision = { ...fixture<CapturedQuestionRevision>('question_revision_2'), question_id: questionId, homework_id: s.hw.id }
    const a: Attempt = { ...fixture<Attempt>('correction_across_question_revision'), id: attemptId, case_id: caseId, question_id: questionId, parent_attempt_id: null, root_attempt_id: attemptId, asset_ids: ['asset_one'] }
    const decision: Decision | null = state ? { ...fixture<Decision>('model_candidate'), id: `decision_page${suffix}`, case_id: caseId, check_id: `check_page${suffix}`, ...state, reason_code: state.validity === 'needs_review' ? 'recognition_conflict' : null, input_versions: { ...fixture<Decision>('model_candidate').input_versions, question_id: questionId, attempt_id: attemptId } } : null
    const c: CorrectionCase = { ...fixture<CorrectionCase>('resolved_case'), id: caseId, scope: s.hw.scope, homework_id: s.hw.id, question_id: questionId, root_attempt_id: attemptId, current_attempt_id: attemptId, current_decision: decision, progress: 'pending', attempt_ids: [attemptId], check_ids: [], hint_ids: [], help_request_ids: [], review_ids: [] }
    expect(valid('CorrectionCase', c), `case ${index}`).toBe(true)
    expect(valid('Attempt', a), `attempt ${index}`).toBe(true)
    expect(valid('CapturedQuestionRevision', q), `question ${index}`).toBe(true)
    return { case: c, question: q, attempt: a }
  })
  const jobs = new Map<string, Job>(), checks = new Map<string, CheckRun>()
  const addCheck = (row: CaseDetail, status: Job['status'], versions: Partial<CheckRun['input_versions']> = {}) => {
    const suffix = String(checks.size + 1), checkId = `check_pipeline${suffix}`
    const created_at = new Date(Date.parse(now) + checks.size * 1000).toISOString()
    const job: Job = { ...s.job, id: `job_pipeline${suffix}`, kind: 'check', status, created_at, result: null, failure: ['failed', 'retryable_failed'].includes(status) ? { code: 'provider_unavailable', message: '合成失败', retryable: status === 'retryable_failed' } : null }
    const check: CheckRun = { id: checkId, revision: 1, job_id: job.id, case_id: row.case.id, input_versions: { question_id: row.question.question_id, question_revision: row.question.revision, attempt_id: row.attempt.id, attempt_recognition_revision: row.attempt.recognition_revision, case_revision_at_submission: row.case.revision, fingerprint: '1'.repeat(64), ...versions }, model_version: null, rule_version: null, prompt_version: null, decision_ids: [], created_at }
    jobs.set(job.id, job); checks.set(check.id, check); row.case.check_ids.push(check.id); s.hw.job_ids.push(job.id)
    const finish = (patch: Partial<Decision> = {}) => {
      const decision: Decision = { ...fixture<Decision>('model_candidate'), id: `decision_pipeline${suffix}`, case_id: row.case.id, check_id: check.id, input_versions: check.input_versions, reason_code: patch.verdict === 'unable_to_judge' ? 'recognition_conflict' : null, ...patch }
      row.case.current_decision = decision; row.case.revision++; check.decision_ids = [decision.id]
      job.status = 'succeeded'; job.failure = null; job.result = { resource_type: 'check', resource_id: check.id }
    }
    if (status === 'succeeded') finish()
    return { job, check, finish }
  }
  s.route((path, init) => {
    if (path === `/v1/homeworks/${s.hw.id}/detail`) return json({
      protocol: 'homework-detail.v1', homework: s.hw, case_records: rows.map(row => row.case),
      questions: rows.map(row => ({ record: row.question, current_revision: row.question.revision, currently_effective: true })),
      attempts: rows.map(row => ({ record: row.attempt, current_revision: row.attempt.revision, currently_effective: true })),
      checks: [...checks.values()], jobs: s.hw.job_ids.map(id => id === s.job.id ? s.job : jobs.get(id)),
    })
    if (path === `/v1/homeworks/${s.hw.id}/recognitions`) { s.hw.case_ids = rows.map(row => row.case.id); s.hw.question_ids = rows.map(row => row.question.question_id) }
    for (const row of rows) {
      if (path === `/v1/cases/${row.case.id}`) return json(row.case)
      if (path === `/v1/questions/${row.question.question_id}`) return json({ record: row.question, current_revision: row.question.revision, currently_effective: true })
      if (path === `/v1/attempts/${row.attempt.id}`) return json({ record: row.attempt, current_revision: row.attempt.revision, currently_effective: true })
      if (path === `/v1/cases/${row.case.id}/checks` && init.method === 'POST') {
        const { job } = addCheck(row, 'succeeded')
        return json({ job, created_resource: job.result }, 202)
      }
    }
    const check = checks.get(path.split('/').at(-1)!); if (path.startsWith('/v1/checks/') && check) return json(check)
    const job = jobs.get(path.split('/').at(-1)!); if (path.startsWith('/v1/jobs/') && job) return json(job)
  })
  return { ...s, rows, addCheck }
}

describe('bounded read-only draft observation', () => {
  const writes = (s: ReturnType<typeof setup>) => s.calls.filter(call => ['POST', 'PUT'].includes(call.init.method ?? 'GET') && !call.path.startsWith('/v1/auth/'))
  it('carries the no-progress window across bounded queued polls, pauses, and refreshes a late result with GETs only', async () => {
    const s = setup(); let clock = 0
    const result = s.job.result
    Object.assign(s.job, { status: 'queued', result: null, retry_after_seconds: 1 })
    const client = new StudentClient({ ...s.options, now: () => clock, sleep: async ms => { clock += ms } })
    await client.login('synthetic.student', 'synthetic password only')
    const draft = await client.createDraft(photo.blob), waiting = await client.runDraft(draft.id)
    expect(waiting).toMatchObject({ stage: 'waiting', observation: { status: 'queued', paused: false, lastProgressAt: 0 } })
    const writeCount = writes(s).length
    clock = NO_PROGRESS_TIMEOUT_MS
    const paused = await client.observeDraft(draft.id)
    expect(paused).toMatchObject({ stage: 'waiting', observation: { status: 'queued', paused: true } })
    expect(await paused.photo!.text()).toBe('synthetic-image')
    expect(client.account?.username).toBe('synthetic.student')
    clock += NO_PROGRESS_TIMEOUT_MS
    expect((await client.observeDraft(draft.id)).observation?.paused).toBe(true)
    expect(writes(s)).toHaveLength(writeCount)
    Object.assign(s.job, { status: 'succeeded', revision: 2, result, retry_after_seconds: null })
    expect(await client.observeDraft(draft.id, true)).toMatchObject({ stage: 'complete', observation: { paused: false, status: null } })
    expect(writes(s)).toHaveLength(writeCount)
  })
  it('shows a real queued-to-running transition and stops a stalled running run without creating another job', async () => {
    const s = setup(); let clock = 0, sawQueued = false, sawRunning = false
    Object.assign(s.job, { status: 'queued', result: null, retry_after_seconds: 2 })
    const client = new StudentClient({ ...s.options, now: () => clock, sleep: async ms => {
      const stored = (await client.listDrafts())[0]
      if (stored.observation?.status === 'queued') { sawQueued = true; s.job.status = 'running'; s.job.revision++ }
      if (stored.stage === 'recognizing' && stored.observation?.status === 'running') sawRunning = true
      clock += ms
    } })
    await client.login('synthetic.student', 'synthetic password only')
    const draft = await client.createDraft(photo.blob), paused = await client.runDraft(draft.id)
    expect(sawQueued && sawRunning).toBe(true)
    expect(paused).toMatchObject({ stage: 'waiting', observation: { status: 'running', paused: true, lastProgressAt: 2000 } })
    expect(s.calls.filter(call => call.path.endsWith('/recognitions'))).toHaveLength(1)
    const refreshed = await client.observeDraft(draft.id, true)
    expect(refreshed.observation).toMatchObject({ paused: false, lastProgressAt: clock })
    const writesBefore = writes(s).length
    const continued = await client.runDraft(draft.id)
    expect(continued.observation?.paused).toBe(true)
    expect(writes(s)).toHaveLength(writesBefore)
  })
  it('resets a history observation window and never starts a missing check from a refresh', async () => {
    const s = wholePageSetup([null]); let clock = 0
    s.hw.case_ids = s.rows.map(row => row.case.id); s.hw.question_ids = s.rows.map(row => row.question.question_id); s.hw.job_ids = [s.job.id]
    const row = s.rows[0]; row.attempt.recognition_status = 'ready'
    const client = new StudentClient({ ...s.options, now: () => clock })
    await client.login('synthetic.student', 'synthetic password only')
    const opened = await client.openHomework(s.hw.id), id = opened.draftId!
    expect((await client.observeDraft(id, true)).observation?.status).toBeNull()
    expect(writes(s)).toHaveLength(0)
    const pending = s.addCheck(row, 'queued')
    await client.observeDraft(id, true)
    clock = NO_PROGRESS_TIMEOUT_MS
    expect((await client.observeDraft(id)).observation?.paused).toBe(true)
    await client.openHomework(s.hw.id)
    expect((await client.getDraft(id)).observation).toMatchObject({ paused: false, status: 'queued', lastProgressAt: clock })
    pending.finish()
    expect((await client.observeDraft(id)).stage).toBe('complete')
    expect(writes(s)).toHaveLength(0)
  })
  it('retains paused question cards and retries terminal failures only on an explicit retry', async () => {
    const s = wholePageSetup([null]); let clock = 0
    s.hw.case_ids = s.rows.map(row => row.case.id); s.hw.question_ids = s.rows.map(row => row.question.question_id); s.hw.job_ids = [s.job.id]
    const row = s.rows[0]; row.attempt.recognition_status = 'ready'
    const pending = s.addCheck(row, 'running')
    const client = new StudentClient({ ...s.options, now: () => clock })
    await client.login('synthetic.student', 'synthetic password only')
    const id = (await client.openHomework(s.hw.id)).draftId!
    clock = NO_PROGRESS_TIMEOUT_MS
    expect((await client.observeDraft(id)).detail?.cases[0]).toMatchObject({ observationPaused: true, case: { id: row.case.id } })
    Object.assign(pending.job, { status: 'failed', revision: 2, failure: { code: 'provider_unavailable', message: 'synthetic', retryable: false } })
    expect((await client.observeDraft(id, true)).stage).toBe('failed')
    expect((await client.runDraft(id)).stage).toBe('failed')
    expect(writes(s)).toHaveLength(0)
    expect((await client.runDraft(id, { retryFailed: true })).stage).toBe('complete')
    expect(writes(s).filter(call => call.path.endsWith('/checks'))).toHaveLength(1)
    await client.observeDraft(id, true)
    expect(writes(s).filter(call => call.path.endsWith('/checks'))).toHaveLength(1)
  })
  it('does not replay an unknown accepted write when refreshing its status', async () => {
    const s = setup(); await s.client.login('synthetic.student', 'synthetic password only')
    const draft = await s.client.createDraft(photo.blob)
    s.loseRecognition(); await s.client.runDraft(draft.id)
    const count = writes(s).length
    await s.client.observeDraft(draft.id, true)
    await s.client.openHomework(s.hw.id)
    expect(writes(s)).toHaveLength(count)
    const scope = JSON.stringify(['http://localhost', 'actor_test', 'student_one'])
    expect(await s.store.hasPending(scope, `${draft.id}:`)).toBe(true)
  })
  it('stops pending card animation when no server job remains to make progress', async () => {
    const s = wholePageSetup([null])
    s.hw.case_ids = s.rows.map(row => row.case.id); s.hw.question_ids = s.rows.map(row => row.question.question_id); s.hw.job_ids = [s.job.id]
    s.rows[0].attempt.recognition_status = 'pending'
    await s.client.login('synthetic.student', 'synthetic password only')
    const opened = await s.client.openHomework(s.hw.id)
    expect(opened.cases[0].observationPaused).toBe(true)
    expect(await s.client.observeDraft(opened.draftId!)).toMatchObject({ stage: 'waiting', observation: { status: null }, detail: { cases: [{ observationPaused: true }] } })
    expect(writes(s)).toHaveLength(0)
  })
})

describe('whole-page persisted correction results', () => {
  const results: Pick<Decision, 'validity' | 'verdict'>[] = [
    { validity: 'candidate', verdict: 'correct' }, { validity: 'candidate', verdict: 'incorrect' },
    { validity: 'needs_review', verdict: 'unable_to_judge' }, { validity: 'needs_review', verdict: 'correct' },
  ]
  it('uses ready candidate and needs_review results without starting per-question checks', async () => {
    const s = wholePageSetup(results); await s.client.login('synthetic.student', 'synthetic password only'); const draft = await s.client.createDraft(photo.blob)
    const result = await s.client.runDraft(draft.id)
    expect(result.error).toBeNull(); expect(result.stage).toBe('complete'); expect(result.detail?.cases).toHaveLength(4)
    expect(s.calls.filter(c => c.path.endsWith('/checks') && c.init.method === 'POST')).toHaveLength(0)
    expect(s.calls.filter(c => c.path.endsWith('/recognitions'))).toHaveLength(1)
    expect(result.detail?.cases.map(row => row.case.current_decision?.validity)).toEqual(results.map(state => state.validity))
    expect(result.detail!.cases.map(decisionLabel)).toEqual(['AI 初判 · 正确', 'AI 初判 · 需要订正', '暂时看不准 · 可以先看其他题', '暂时看不准 · 可以先看其他题'])
  })
  it('does not swallow an explicit recheck behind an unfinished background run', async () => {
    const s = wholePageSetup([{ validity: 'candidate', verdict: 'correct' }])
    const completedResult = s.job.result
    s.job.status = 'running'; s.job.result = null
    let entered!: () => void, release!: () => void
    const polling = new Promise<void>(resolve => { entered = resolve })
    const gate = new Promise<void>(resolve => { release = resolve })
    const client = new StudentClient({ ...s.options, sleep: async () => { entered(); await gate } })
    await client.login('synthetic.student', 'synthetic password only')
    const draft = await client.createDraft(photo.blob), background = client.runDraft(draft.id)
    await polling
    const explicit = client.runDraft(draft.id, { forceCheckCaseIds: [s.rows[0].case.id], forceCheckToken: 'subject_change' })
    s.job.status = 'succeeded'; s.job.result = completedResult; release()
    expect((await background).stage).toBe('complete')
    expect((await explicit).stage).toBe('complete')
    expect(s.calls.filter(call => call.path.endsWith('/checks') && call.init.method === 'POST')).toHaveLength(1)
  })
  it('keeps the photo and page decisions after a lost response, then resumes after reload without duplicate checks', async () => {
    const s = wholePageSetup(results); await s.client.login('synthetic.student', 'synthetic password only'); const draft = await s.client.createDraft(photo.blob)
    s.loseRecognition(); const failed = await s.client.runDraft(draft.id); expect(failed.stage).toBe('failed')
    const scope = JSON.stringify(['http://localhost', 'actor_test', 'student_one'])
    const saved = await s.store.draft(scope, draft.id)
    expect(await saved.pages[0].photo.blob.text()).toBe('synthetic-image'); expect(saved.detail?.cases).toHaveLength(4)
    const restored = new StudentClient(s.options); await restored.restore(); const opened = await restored.openHomework(s.hw.id)
    const result = await restored.runDraft(opened.draftId!)
    expect(result.stage).toBe('complete'); expect(result.detail?.cases[2].case.current_decision?.validity).toBe('needs_review')
    const requests = s.calls.filter(c => c.path.endsWith('/recognitions'))
    expect(requests).toHaveLength(2); expect(requests[1].init.headers).toEqual(requests[0].init.headers); expect(requests[1].init.body).toBe(requests[0].init.body)
    expect(s.calls.filter(c => c.path.endsWith('/checks') && c.init.method === 'POST')).toHaveLength(0)
    expect(s.calls.filter(c => c.init.method === 'PUT')).toHaveLength(1)
    expect(await s.store.hasPending(scope, `${draft.id}:`)).toBe(false)
  })
  it.each(['missing', 'stale', 'retracted', 'old_recognition'] as const)('retains legacy checks for %s decisions while preserving completed page items', async state => {
    const s = wholePageSetup([{ validity: 'needs_review', verdict: 'unable_to_judge' }, state === 'missing' ? null : { validity: 'candidate', verdict: 'correct' }])
    const d = s.rows[1].case.current_decision
    if (d && (state === 'stale' || state === 'retracted')) d.validity = state
    if (d && state === 'old_recognition') d.input_versions.attempt_recognition_revision = 2
    await s.client.login('synthetic.student', 'synthetic password only'); const draft = await s.client.createDraft(photo.blob)
    const result = await s.client.runDraft(draft.id)
    expect(result.error).toBeNull(); expect(result.stage).toBe('complete')
    expect(s.calls.filter(c => c.path.endsWith('/checks') && c.init.method === 'POST').map(c => c.path)).toEqual(['/v1/cases/case_page2/checks'])
    expect(result.detail?.cases[0].case.current_decision?.validity).toBe('needs_review')
  })
})

describe('recognition followed by per-question checks', () => {
  const scope = JSON.stringify(['http://localhost', 'actor_test', 'student_one'])
  const posts = (s: ReturnType<typeof wholePageSetup>) => s.calls.filter(call => call.path.endsWith('/checks') && call.init.method === 'POST')
  const openRecognized = async (s: ReturnType<typeof wholePageSetup>, client = s.client) => {
    s.hw.case_ids = s.rows.map(row => row.case.id); s.hw.question_ids = s.rows.map(row => row.question.question_id)
    s.hw.job_ids.unshift(s.job.id)
    await client.login('synthetic.student', 'synthetic password only')
    return (await client.openHomework(s.hw.id)).draftId!
  }
  const asCorrection = async (s: ReturnType<typeof wholePageSetup>, id: string) => {
    const saved = await s.store.draft(scope, id), row = s.rows[0]
    saved.correction = { caseId: row.case.id, parentAttemptId: row.attempt.id, reportedHelp: 'unknown' }
    await s.store.saveDraft(saved)
  }

  it.each([false, true])('publishes settled questions and the next running check without refreshing unchanged rounds (snapshot=%s)', async detailSnapshotEnabled => {
    const s = wholePageSetup([null, null]), first = s.addCheck(s.rows[0], 'running'), second = s.addCheck(s.rows[1], 'queued')
    s.hw.asset_ids = ['asset_one', 'asset_original', 'asset_correction']
    const progress: DraftView[] = [], delays: number[] = [], detailReads: number[] = []
    let id = ''
    const client = new StudentClient({ ...s.options, detailSnapshotEnabled, sleep: async ms => {
      delays.push(ms); progress.push(await client.getDraft(id))
      detailReads.push(s.calls.filter(call => call.path === `/v1/homeworks/${s.hw.id}` || call.path === `/v1/homeworks/${s.hw.id}/detail`).length)
      if (progress.length === 1) { first.finish(); second.job.status = 'running'; second.job.revision++ }
      if (progress.length === 3) second.finish()
    } })
    id = await openRecognized(s, client)
    const notified: Promise<StoredDraft>[] = []
    const unsubscribe = client.subscribe(() => { notified.push(s.store.draft(scope, id)) })
    const result = await client.runDraft(id); unsubscribe()
    expect(result.stage).toBe('complete'); expect(result.error).toBeNull()
    expect(progress).toHaveLength(3)
    expect(progress[1].detail?.cases.map(row => row.checkJob?.status)).toEqual(['succeeded', 'running'])
    expect(progress[1].detail?.cases.map(row => effectiveDecision(row)?.verdict ?? null)).toEqual(['correct', null])
    expect((await Promise.all(notified)).some(d => d.detail?.cases[0].case.current_decision && !d.detail.cases[1].case.current_decision)).toBe(true)
    expect(detailReads[2]).toBe(detailReads[1])
    expect(delays).toEqual([2000, 2000, 2000]); expect(posts(s)).toHaveLength(0)
  })

  it('does not attach a settled old-input check to a newer recognition revision during progress', async () => {
    const s = wholePageSetup([null, null]), first = s.addCheck(s.rows[0], 'running'), second = s.addCheck(s.rows[1], 'queued')
    const progress: DraftView[] = []
    let id = ''
    const client = new StudentClient({ ...s.options, sleep: async () => {
      progress.push(await client.getDraft(id))
      if (progress.length === 1) {
        first.finish(); s.rows[0].attempt.recognition_revision++; s.rows[0].attempt.revision++
        s.rows[0].attempt.answer_text = 'newly read answer'; s.rows[0].case.revision++
      } else second.finish()
    } })
    id = await openRecognized(s, client)
    const result = await client.runDraft(id)
    expect(result.error).toBeNull(); expect(result.stage).toBe('checking')
    const updated = progress[1].detail!.cases[0]
    expect(updated.attempt.answer_text).toBe('newly read answer')
    expect(updated.checkJob).toBeUndefined(); expect(effectiveDecision(updated)).toBeNull()
    expect(posts(s)).toHaveLength(0)
  })

  it.each(['failed', 'superseded', 'cancelled'] as const)('publishes a settled %s check while the next question is still queued', async status => {
    const s = wholePageSetup([null, null]), first = s.addCheck(s.rows[0], 'running'), second = s.addCheck(s.rows[1], 'queued')
    const progress: DraftView[] = []
    let id = ''
    const client = new StudentClient({ ...s.options, sleep: async () => {
      progress.push(await client.getDraft(id))
      if (progress.length === 1) {
        first.job.status = status; first.job.revision++
        if (status === 'failed') first.job.failure = { code: 'provider_unavailable', message: '合成失败', retryable: false }
      } else second.finish()
    } })
    id = await openRecognized(s, client)
    const result = await client.runDraft(id)
    expect(progress[1].detail?.cases.map(row => row.checkJob?.status)).toEqual([status, 'queued'])
    expect(progress[1].detail?.cases[0].case.current_decision).toBeNull()
    expect(result).toMatchObject({ stage: 'failed', error: { code: `job_${status}` } })
    expect(posts(s)).toHaveLength(0)
  })

  it.each(['disconnect', 'login', 'restore'] as const)('rejects an incremental refresh crossing %s without overwriting the newer cache', async transition => {
    const s = wholePageSetup([null, null]), first = s.addCheck(s.rows[0], 'running'), second = s.addCheck(s.rows[1], 'queued')
    let enter!: () => void, release!: () => void, refresh = false
    const entered = new Promise<void>(resolve => { enter = resolve }), gate = new Promise<void>(resolve => { release = resolve })
    const client = new StudentClient({ ...s.options,
      sleep: async () => { first.finish(); refresh = true },
      transport: async (input, init) => {
        const response = await s.options.transport(input, init)
        if (refresh && new URL(String(input)).pathname === `/v1/attempts/${s.rows[0].attempt.id}`) {
          refresh = false; enter(); await gate
        }
        return response
      },
    })
    const id = await openRecognized(s, client), run = client.runDraft(id).catch(error => error)
    await entered
    let saved: StoredDraft
    try {
      if (transition === 'disconnect') client.disconnect()
      else {
        if (transition === 'restore') await client.restore()
        else await client.login('synthetic.student', 'synthetic password only')
        second.finish(); await client.openHomework(s.hw.id)
      }
      saved = await s.store.draft(scope, id)
    } finally { release() }
    expect(await run).toMatchObject({ code: 'session_changed' })
    expect(await s.store.draft(scope, id)).toEqual(saved)
    expect(posts(s)).toHaveLength(0)
  })

  it.each(['ready', 'needs_revision'] as const)('checks a %s answer after recognition and leaves completed unresolved results alone', async status => {
    const s = wholePageSetup([null, { validity: 'needs_review', verdict: 'unable_to_judge' }])
    s.rows[0].attempt.recognition_status = status; s.rows[0].attempt.answer_text = '3x-6'
    await s.client.login('synthetic.student', 'synthetic password only')
    const draft = await s.client.createDraft(photo.blob), result = await s.client.runDraft(draft.id)
    expect(result.error).toBeNull(); expect(result.stage).toBe('complete')
    expect(posts(s).map(call => call.path)).toEqual([`/v1/cases/${s.rows[0].case.id}/checks`])
    expect(JSON.parse(posts(s)[0].init.body as string)).toMatchObject({ attempt_id: s.rows[0].attempt.id, question_revision: s.rows[0].question.revision, attempt_recognition_revision: s.rows[0].attempt.recognition_revision })
    await s.client.runDraft(draft.id)
    expect(posts(s)).toHaveLength(1)
    expect(result.detail?.cases[1].case.current_decision?.verdict).toBe('unable_to_judge')
  })

  it.each(['queued', 'running', 'retryable_failed'] as const)('adopts an existing %s check before inspecting a needs-revision answer', async status => {
    const s = wholePageSetup([null]), row = s.rows[0]
    row.attempt.recognition_status = 'needs_revision'; row.attempt.answer_text = null
    const existing = s.addCheck(row, status), id = await openRecognized(s)
    const result = await s.client.runDraft(id)
    expect(result.error).toBeNull(); expect(result.stage).toBe('waiting'); expect(posts(s)).toHaveLength(0)
    expect(s.calls.filter(call => call.path === `/v1/jobs/${existing.job.id}`).length).toBeGreaterThanOrEqual(120)
    existing.finish({ validity: 'needs_review', verdict: 'unable_to_judge', correct_answer: null })
    expect((await s.client.runDraft(id)).stage).toBe('complete')
    expect(posts(s)).toHaveLength(0)
  })

  it('observes a newer active check ahead of an older effective decision', async () => {
    const s = wholePageSetup([{ validity: 'candidate', verdict: 'correct' }])
    const existing = s.addCheck(s.rows[0], 'running'), id = await openRecognized(s)
    expect((await s.client.runDraft(id)).stage).toBe('waiting'); expect(posts(s)).toHaveLength(0)
    existing.finish({ verdict: 'incorrect', correct_answer: '3x-6' })
    const done = await s.client.runDraft(id)
    expect(done.stage).toBe('complete'); expect(done.detail?.cases[0].case.current_decision?.correct_answer).toBe('3x-6')
    expect(posts(s)).toHaveLength(0)
  })

  it.each(['failed', 'superseded', 'cancelled'] as const)('keeps the current %s check failed until an explicit retry, even with an older decision', async status => {
    const s = wholePageSetup([{ validity: 'candidate', verdict: 'correct' }]), row = s.rows[0]
    row.attempt.recognition_status = 'needs_revision'; row.attempt.answer_text = '3x-6'
    s.addCheck(row, status)
    const id = await openRecognized(s)
    for (let run = 0; run < 2; run++) expect(await s.client.runDraft(id)).toMatchObject({ stage: 'failed', error: { code: `job_${status}` } })
    expect(posts(s)).toHaveLength(0)
    expect((await s.client.runDraft(id, { retryFailed: true })).stage).toBe('complete')
    expect(posts(s)).toHaveLength(1)
    expect((await s.client.runDraft(id)).stage).toBe('complete'); expect(posts(s)).toHaveLength(1)
  })

  it('does not let an older failed check override the latest completed unable result', async () => {
    const s = wholePageSetup([null]), row = s.rows[0]
    s.addCheck(row, 'failed')
    s.addCheck(row, 'succeeded').finish({ validity: 'needs_review', verdict: 'unable_to_judge', correct_answer: null })
    row.case.check_ids.reverse()
    const id = await openRecognized(s)
    expect((await s.client.runDraft(id)).stage).toBe('complete')
    expect(posts(s)).toHaveLength(0)
  })

  it('ignores failed checks of an older input revision and submits the current answer once', async () => {
    const s = wholePageSetup([null]), row = s.rows[0]
    row.attempt.recognition_status = 'needs_revision'; row.attempt.answer_text = '3x-6'
    s.addCheck(row, 'failed', { attempt_recognition_revision: row.attempt.recognition_revision + 1 })
    const id = await openRecognized(s)
    expect((await s.client.runDraft(id)).stage).toBe('complete'); expect(posts(s)).toHaveLength(1)
  })

  it('keeps a mixed homework waiting when one needs-revision answer is still missing', async () => {
    const s = wholePageSetup([null, null])
    for (const row of s.rows) row.attempt.recognition_status = 'needs_revision'
    s.rows[0].attempt.answer_text = '3x-6'; s.rows[1].attempt.answer_text = '  '
    const id = await openRecognized(s)
    const result = await s.client.runDraft(id)
    expect(result.error).toBeNull(); expect(result.stage).toBe('waiting')
    expect(posts(s).map(call => call.path)).toEqual([`/v1/cases/${s.rows[0].case.id}/checks`])
    expect(result.detail?.cases[1].case.current_decision).toBeNull()
  })

  it.each(['queued', 'failed'] as const)('notices a %s check that appears during the final detail refresh', async status => {
    const s = wholePageSetup([{ validity: 'candidate', verdict: 'correct' }]), row = s.rows[0]
    const id = await openRecognized(s)
    let homeworkReads = 0
    const client = new StudentClient({ ...s.options, transport: async (input, init) => {
      if (new URL(String(input)).pathname === `/v1/homeworks/${s.hw.id}` && ++homeworkReads === 4) s.addCheck(row, status)
      return s.options.transport(input, init)
    } })
    await client.restore()
    const result = await client.runDraft(id)
    expect(result.stage).toBe(status === 'queued' ? 'waiting' : 'failed')
    expect(posts(s)).toHaveLength(0)
  })

  it.each(['queued', 'failed'] as const)('checks only the correction target while another question has a %s check', async status => {
    const s = wholePageSetup([null, null]), [target, other] = s.rows
    target.attempt.recognition_status = 'needs_revision'; target.attempt.answer_text = '3x-6'
    s.addCheck(other, status)
    const id = await openRecognized(s); await asCorrection(s, id)
    const result = await s.client.runDraft(id, { forceCheckCaseIds: [other.case.id], forceCheckToken: 'foreign_target' })
    expect(result.error).toBeNull(); expect(result.stage).toBe('complete')
    expect(posts(s).map(call => call.path)).toEqual([`/v1/cases/${target.case.id}/checks`])
    expect(result.detail?.cases[1].case.current_decision).toBeNull()
  })

  it.each(['queued', 'failed'] as const)('does not complete an unreadable correction with a %s target check', async status => {
    const s = wholePageSetup([null]), target = s.rows[0]
    target.attempt.recognition_status = 'needs_revision'; target.attempt.answer_text = null
    s.addCheck(target, status)
    const id = await openRecognized(s); await asCorrection(s, id)
    expect((await s.client.runDraft(id)).stage).toBe(status === 'queued' ? 'waiting' : 'failed')
    expect(posts(s)).toHaveLength(0)
  })

  it('allows a terminal unreadable correction without a check but keeps pending recognition waiting', async () => {
    const s = wholePageSetup([null]), target = s.rows[0]
    target.attempt.recognition_status = 'pending'; target.attempt.answer_text = null
    const id = await openRecognized(s); await asCorrection(s, id)
    expect((await s.client.runDraft(id)).stage).toBe('waiting')
    target.attempt.recognition_status = 'needs_revision'
    expect((await s.client.runDraft(id)).stage).toBe('complete'); expect(posts(s)).toHaveLength(0)
  })
})

function correctionSetup(wholePageAssessment = false) {
  const s = setup(), c = fixture<CorrectionCase>('resolved_case'), q = fixture<CapturedQuestionRevision>('question_revision_2')
  let a = fixture<Attempt>('correction_across_question_revision')
  c.scope = s.hw.scope; c.homework_id = s.hw.id; c.current_decision = null; c.progress = 'editing'; c.check_ids = []; c.attempt_ids = [a.id]; q.homework_id = s.hw.id
  s.hw.case_ids = [c.id]; s.hw.question_ids = [q.question_id]
  const jobs = new Map<string, Job>(), checks = new Map<string, CheckRun>(); let count = 0, checkCount = 0
  s.route((path, init) => {
    if (path === `/v1/cases/${c.id}`) return json(c)
    if (path === `/v1/questions/${q.question_id}`) return json({ record: q, current_revision: q.revision, currently_effective: true })
    if (path === `/v1/attempts/${a.id}`) return json({ record: a, current_revision: a.revision, currently_effective: true })
    if (path === `/v1/cases/${c.id}/attempts`) {
      const payload = JSON.parse(init.body as string); expect(payload.parent_attempt_id).toBe(a.id); count++
      a = { ...a, id: `attempt_retry${count}`, parent_attempt_id: a.id, recognition_status: count === 1 ? 'failed' : 'needs_revision', answer_text: null, answer_regions: [] }
      c.current_attempt_id = a.id; c.attempt_ids.push(a.id); c.revision++
      const job: Job = { ...s.job, id: `job_correction${count}`, kind: 'recognize_attempt', status: count === 1 ? 'failed' : 'succeeded', failure: count === 1 ? { code: 'invalid_provider_output', message: '合成失败', retryable: false } : null, result: count === 1 ? null : { resource_type: 'attempt', resource_id: a.id } }
      if (wholePageAssessment) {
        a.recognition_status = 'ready'; a.answer_text = '3x-6'; a.asset_ids = payload.asset_ids
        a.recognition_revision = 1; a.recognition_uncertainties = []
        job.status = 'succeeded'; job.failure = null; job.result = { resource_type: 'attempt', resource_id: a.id }
        c.current_decision = { ...fixture<Decision>('model_candidate'), case_id: c.id, verdict: 'correct', validity: 'candidate', input_versions: { question_id: q.question_id, question_revision: q.revision, attempt_id: a.id, attempt_recognition_revision: a.recognition_revision, case_revision_at_submission: c.revision, fingerprint: '1'.repeat(64) } }
      }
      jobs.set(job.id, job); s.hw.job_ids.push(job.id)
      return json({ job, created_resource: { resource_type: 'attempt', resource_id: a.id } })
    }
    if (path === `/v1/cases/${c.id}/checks`) {
      const payload = JSON.parse(init.body as string) as { attempt_id: string; question_revision: number; attempt_recognition_revision: number }
      expect(payload.attempt_id).toBe(a.id); expect(payload.question_revision).toBe(q.revision); expect(payload.attempt_recognition_revision).toBe(a.recognition_revision); checkCount++
      const job: Job = { ...s.job, id: `job_check${checkCount}`, kind: 'check', status: 'succeeded', result: { resource_type: 'check', resource_id: `check_retry${checkCount}` } }
      const checkId = `check_retry${checkCount}`, check: CheckRun = { id: checkId, revision: 1, job_id: job.id, case_id: c.id, input_versions: { question_id: q.question_id, question_revision: q.revision, attempt_id: a.id, attempt_recognition_revision: a.recognition_revision, case_revision_at_submission: c.revision, fingerprint: '1'.repeat(64) }, model_version: null, rule_version: null, prompt_version: null, decision_ids: [`decision_retry${checkCount}`], created_at: now }
      const decision = fixture<Decision>('model_candidate'); decision.id = `decision_retry${checkCount}`; decision.case_id = c.id; decision.check_id = checkId; decision.input_versions = check.input_versions; c.current_decision = decision; c.progress = 'needs_correction'; c.check_ids.push(checkId); c.revision++
      jobs.set(job.id, job); checks.set(checkId, check); s.hw.job_ids.push(job.id)
      return json({ job, created_resource: { resource_type: 'check', resource_id: checkId } }, 202)
    }
    if (path.startsWith(`/v1/checks/`)) return json(checks.get(path.split('/').at(-1)!))
    const job = jobs.get(path.split('/').at(-1)!); if (path.startsWith('/v1/jobs/') && job) return json(job)
  })
  return { ...s, c, q, current: () => a, replaceAttempt: () => { a.id = 'attempt_newer'; c.current_attempt_id = a.id } }
}

it('submits a new photo to the selected original question and reuses its whole-page assessment', async () => {
  const s = correctionSetup(true); await s.client.login('synthetic.student', 'synthetic password only')
  const parent = s.current().id
  const draft = await s.client.createCorrection(s.c.id, photo.blob, 'unknown', parent)
  const result = await s.client.runDraft(draft.id)
  expect(result.error).toBeNull(); expect(result.stage).toBe('complete')
  expect(result.homeworkId).toBe(s.hw.id); expect(result.correction?.caseId).toBe(s.c.id)
  expect(s.current().id).not.toBe(parent); expect(s.current().parent_attempt_id).toBe(parent)
  expect(s.current().asset_ids).toEqual(['asset_one'])
  expect(result.detail?.cases[0].case.current_decision?.input_versions.attempt_id).toBe(s.current().id)
  expect(result.detail?.cases[0].case.current_decision?.verdict).toBe('correct')
  expect(s.calls.filter(call => call.init.method === 'POST' && (call.path.endsWith('/homeworks') || call.path.endsWith('/checks')))).toHaveLength(0)
  expect(s.calls.filter(call => call.path.endsWith('/attempts'))).toHaveLength(1)
})

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
