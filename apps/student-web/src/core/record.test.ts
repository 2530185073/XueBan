import { describe, expect, it, vi } from 'vitest'
import { IDBFactory } from 'fake-indexeddb'
import examples from '../../../../contracts/phase1/examples.json'
import { StudentClient } from './student'
import { RecognitionRecoveryController } from './recognitionRecovery'
import { DraftStore, LOCAL_TTL_MS } from './store'
import type { RecognitionRecoveryStatus, RecognitionRecheckAccepted, Attempt, CapturedQuestionRevision, CheckRun, CorrectionCase, Decision, HelpRequest, Hint, HomeworkSession, Job, JournalEntry, NormalizedPhoto, Review, StoredDraft } from './types'

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
const problem = (status: 401 | 403, code: string) => json({ type: 'urn:xueban:authorization', title: '合成授权状态', status, category: 'authorization', code, detail: '合成', request_id: 'request_test', current_revision: null, retry_after_seconds: null }, status)
const photo: NormalizedPhoto = { blob: new Blob(['synthetic-image'], { type: 'image/jpeg' }), width: 20, height: 20, sha256: 'a'.repeat(64), mime: 'image/jpeg', size: 15 }
const scopeOf = (actor = 'actor_test', student = 'student_one') => JSON.stringify(['http://localhost', actor, student])
const daysAgo = (days: number, now = Date.now()) => new Date(now - days * 86_400_000).toISOString()

function harness(store = new DraftStore(new IDBFactory())) {
  const storage = new MemorySession()
  const identity = { actor_id: 'actor_test', roles: ['student'], authorized_students: [{ student_id: 'student_one', display_name: '合成学生', campus_id: 'campus_one' }] }
  const scope = { tenant_id: 'tenant_one', campus_id: 'campus_one', student_id: 'student_one' }
  const hw: HomeworkSession = { ...fixture<HomeworkSession>('homework'), id: 'homework_one', scope, asset_ids: [], question_ids: [], case_ids: [], job_ids: [] }
  const reads = new Map<string, () => unknown>(), calls: { path: string; init: RequestInit }[] = []
  let me: () => Response = () => json(identity)
  let write: ((path: string, init: RequestInit) => Response | undefined) | undefined
  const transport = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const path = new URL(String(input)).pathname; calls.push({ path, init })
    if (path === '/v1/auth/login') return json({ access_token: 'synthetic.access.token', expires_at: '2099-01-01T00:00:00Z', must_change_password: false })
    if (path === '/v1/auth/account') return json({ username: 'synthetic.student', student_id: 'student_one', must_change_password: false })
    if (path === '/v1/auth/logout') return json({ logged_out: true })
    if (path === '/v1/me') return me()
    if (path === `/v1/homeworks/${hw.id}`) return json(hw)
    if (init.method === 'POST') { const response = write?.(path, init); if (response) return response }
    const read = reads.get(path); if (read) return json(read())
    throw new Error(`Unexpected synthetic route ${path}`)
  }) as unknown as typeof fetch
  const options = { origin: 'http://localhost', storage, store, transport, normalize: async () => photo, sleep: async () => undefined }
  const addCase = (id: string, patch: Partial<CorrectionCase> = {}) => {
    const questionId = `question_${id.slice(5)}`, attemptId = `attempt_${id.slice(5)}`
    const c: CorrectionCase = { ...fixture<CorrectionCase>('resolved_case'), id, scope, homework_id: hw.id, question_id: questionId, current_question_revision: 1, root_attempt_id: attemptId, current_attempt_id: attemptId, progress: 'pending', current_decision: null, attempt_ids: [attemptId], check_ids: [], hint_ids: [], help_request_ids: [], review_ids: [], lifecycle: 'active', replacement_case_ids: [], ...patch }
    const q: CapturedQuestionRevision = { ...fixture<CapturedQuestionRevision>('question_revision_2'), question_id: questionId, revision: 1, homework_id: hw.id }
    const a: Attempt = { ...fixture<Attempt>('original_attempt'), id: attemptId, case_id: id, question_id: questionId, root_attempt_id: attemptId }
    reads.set(`/v1/cases/${id}`, () => c)
    reads.set(`/v1/questions/${questionId}`, () => ({ record: q, current_revision: 1, currently_effective: true }))
    reads.set(`/v1/attempts/${attemptId}`, () => ({ record: a, current_revision: 1, currently_effective: true }))
    return { c, q, a }
  }
  const decisionFor = (c: CorrectionCase, a: Attempt, patch: Partial<Decision> = {}): Decision => {
    const d = fixture<Decision>('model_candidate')
    return { ...d, id: `decision_${c.id.slice(5)}`, case_id: c.id, check_id: `check_${c.id.slice(5)}`, input_versions: { ...d.input_versions, question_id: c.question_id, question_revision: c.current_question_revision, attempt_id: a.id, attempt_recognition_revision: a.recognition_revision }, ...patch }
  }
  return { options, client: new StudentClient(options), storage, store, hw, reads, calls, addCase, decisionFor, setMe: (next: () => Response) => { me = next }, onWrite: (next: typeof write) => { write = next } }
}
const login = (client: StudentClient) => client.login('synthetic.student', 'synthetic password only')

describe('latest page job recovery', () => {
  const job = (id: string, created_at: string, status: Job['status']): Job => ({ id, revision: 1, kind: 'recognize_homework', status, created_at, updated_at: created_at, retry_after_seconds: null, failure: status === 'failed' ? { code: 'provider_unavailable', message: '合成失败', retryable: false } : null, result: status === 'succeeded' ? { resource_type: 'homework', resource_id: 'homework_one' } : null })
  it('replaces an older failed local job with the newer successful job regardless of UUID order and does not POST again', async () => {
    const s = harness(), { c, a } = s.addCase('case_one')
    c.current_decision = s.decisionFor(c, a); s.hw.case_ids = [c.id]
    const old = job('job_zfailed', '2026-10-18T09:00:00.123100Z', 'failed'), latest = job('job_asuccess', '2026-10-18T09:00:00.123200Z', 'succeeded')
    s.reads.set(`/v1/jobs/${old.id}`, () => old); s.reads.set(`/v1/jobs/${latest.id}`, () => latest)
    s.hw.job_ids = [old.id]
    await login(s.client)
    const first = await s.client.openHomework(s.hw.id)
    const failed = await s.store.draft(scopeOf(), first.draftId!)
    expect(failed.stage).toBe('failed'); expect(failed.recognitionJobId).toBe(old.id)
    failed.error = { code: 'job_failed', message: '过期失败提示' }; await s.store.saveDraft(failed)
    s.hw.job_ids = [latest.id, old.id]
    const restored = new StudentClient(s.options); await restored.restore()
    const opened = await restored.openHomework(s.hw.id), saved = await s.store.draft(scopeOf(), opened.draftId!)
    expect(saved.stage).toBe('complete'); expect(saved.recognitionJobId).toBe(latest.id); expect(saved.error).toBeNull()
    expect((await restored.runDraft(opened.draftId!)).stage).toBe('complete')
    expect(s.calls.filter(call => call.init.method === 'POST').map(call => call.path)).toEqual(['/v1/auth/login'])
  })
  it('keeps a newer queued recognition waiting even when an older successful result exists', async () => {
    const s = harness(), { c, a } = s.addCase('case_one')
    c.current_decision = s.decisionFor(c, a); s.hw.case_ids = [c.id]
    const old = job('job_zsuccess', '2026-10-18T09:00:00.123100Z', 'succeeded'), latest = job('job_aqueued', '2026-10-18T09:00:00.123200Z', 'queued')
    s.reads.set(`/v1/jobs/${old.id}`, () => old); s.reads.set(`/v1/jobs/${latest.id}`, () => latest); s.hw.job_ids = [latest.id, old.id]
    await login(s.client)
    const opened = await s.client.openHomework(s.hw.id), saved = await s.store.draft(scopeOf(), opened.draftId!)
    expect(saved.stage).toBe('waiting'); expect(saved.recognitionJobId).toBe(latest.id)
    expect(s.calls.filter(call => call.init.method === 'POST').map(call => call.path)).toEqual(['/v1/auth/login'])
  })
})

describe('layout repair: superseded cases', () => {
  it('hides superseded cases and shows their replacements in place, without reading retired questions', async () => {
    const s = harness()
    s.addCase('case_old', { lifecycle: 'superseded', replacement_case_ids: ['case_new'] }); s.addCase('case_new'); s.addCase('case_other')
    s.hw.case_ids = ['case_old', 'case_other']
    await login(s.client)
    const detail = await s.client.openHomework(s.hw.id)
    expect(detail.cases.map(r => r.case.id)).toEqual(['case_new', 'case_other'])
    expect(detail.retired).toEqual([{ id: 'case_old', lifecycle: 'superseded', replacementCaseIds: ['case_new'] }])
    expect(s.calls.some(c => c.path === '/v1/questions/question_old' || c.path === '/v1/attempts/attempt_old')).toBe(false)
    const summary = await s.client.homeworkSummary(s.hw)
    expect(summary.counts.total).toBe(2); expect(summary.retired).toBe(1); expect(summary.firstUnresolvedCaseId).toBe('case_new')
    await expect(s.client.requestHelp('case_old', 'question', '请老师看看')).rejects.toMatchObject({ code: 'case_superseded' })
  })
  it('summarises a history page under one authorization and isolates a failing homework', async () => {
    const s = harness(); s.addCase('case_one'); s.hw.case_ids = ['case_one']
    const broken = { ...s.hw, id: 'homework_broken', case_ids: ['case_missing'] }
    await login(s.client); const before = s.calls.filter(c => c.path === '/v1/me').length, got: [string, number | null][] = []
    await s.client.homeworkSummaries([s.hw, broken], (id, summary) => got.push([id, summary?.counts.total ?? null]))
    expect(got.sort()).toEqual([['homework_broken', null], ['homework_one', 1]])
    expect(s.calls.filter(c => c.path === '/v1/me').length - before).toBe(1)
  })
  it('does not count a replacement listed both in the homework and as a replacement twice', async () => {
    const s = harness()
    s.addCase('case_old', { lifecycle: 'superseded', replacement_case_ids: ['case_new', 'case_extra'] }); s.addCase('case_new'); const extra = s.addCase('case_extra')
    extra.q.created_at = extra.c.created_at = '2026-10-16T08:00:01Z'
    s.hw.case_ids = ['case_old', 'case_new', 'case_extra']
    await login(s.client)
    expect((await s.client.openHomework(s.hw.id)).cases.map(r => r.case.id)).toEqual(['case_new', 'case_extra'])
  })
  it('orders page questions by creation time rather than random case IDs, including microseconds and refreshes', async () => {
    const s = harness(), first = s.addCase('case_zearly'), middle = s.addCase('case_mmiddle'), last = s.addCase('case_alate')
    first.q.created_at = first.c.created_at = '2026-10-18T09:00:00.123100+00:00'
    middle.q.created_at = middle.c.created_at = '2026-10-18T09:00:00.123200+00:00'
    last.q.created_at = last.c.created_at = '2026-10-18T09:00:00.123300+00:00'
    s.hw.case_ids = [last.c.id, middle.c.id, first.c.id]
    await login(s.client)
    const expected = [first.c.id, middle.c.id, last.c.id]
    expect((await s.client.openHomework(s.hw.id)).cases.map(row => row.case.id)).toEqual(expected)
    s.hw.case_ids = [middle.c.id, first.c.id, last.c.id]
    expect((await s.client.openHomework(s.hw.id)).cases.map(row => row.case.id)).toEqual(expected)
    expect((await s.client.homeworkSummary(s.hw)).firstUnresolvedCaseId).toBe(first.c.id)
  })
  it('keeps newly repaired questions in the older original question position', async () => {
    const s = harness(), original = s.addCase('case_original', { lifecycle: 'superseded', replacement_case_ids: ['case_replacement'] }), later = s.addCase('case_later'), replacement = s.addCase('case_replacement')
    original.c.created_at = '2026-10-18T09:00:00Z'
    later.q.created_at = later.c.created_at = '2026-10-18T09:00:01Z'
    replacement.q.created_at = replacement.c.created_at = '2026-10-19T09:00:00Z'
    s.hw.case_ids = [later.c.id, replacement.c.id, original.c.id]
    await login(s.client)
    expect((await s.client.openHomework(s.hw.id)).cases.map(row => row.case.id)).toEqual([replacement.c.id, later.c.id])
  })
})

describe('appeal and help requests', () => {
  function appealSetup() {
    const s = harness(), { c, a } = s.addCase('case_one')
    c.current_decision = s.decisionFor(c, a); c.progress = 'needs_correction'; s.hw.case_ids = [c.id]
    const created: HelpRequest[] = []; let lose = true
    s.reads.set('/v1/help-requests/help_one', () => created[0]); s.reads.set('/v1/help-requests/help_two', () => created[1])
    s.onWrite((path, init) => {
      if (path !== `/v1/cases/${c.id}/help-requests`) return undefined
      const body = JSON.parse(init.body as string), key = (init.headers as Record<string, string>)['Idempotency-Key']
      let help = created.find(h => (h as HelpRequest & { key?: string }).key === key)
      if (!help) {
        help = { ...fixture<HelpRequest>('resolved_help'), id: created.length ? 'help_two' : 'help_one', revision: 1, case_id: c.id, attempt_id: body.attempt_id, kind: body.kind, note: body.note, status: 'waiting', claimed_by: null, claimed_at: null, completed_at: null, resolution_note: null }
        Object.defineProperty(help, 'key', { value: key, enumerable: false }); created.push(help); c.help_request_ids = [...c.help_request_ids, help.id]; c.revision++
      }
      if (lose) { lose = false; throw new TypeError('synthetic lost help response') }
      return json(help, 201)
    })
    return { ...s, c, a, created }
  }
  it('replays an unknown appeal with the original key and note, then blocks a duplicate while open', async () => {
    const s = appealSetup(); await login(s.client)
    await expect(s.client.requestHelp(s.c.id, 'appeal', '我觉得是对的')).rejects.toMatchObject({ code: 'network_unknown' })
    const help = await s.client.requestHelp(s.c.id, 'appeal', '换了一种说法')
    expect(help).toMatchObject({ id: 'help_one', kind: 'appeal', note: '我觉得是对的', status: 'waiting' })
    const posts = () => s.calls.filter(c => c.path.endsWith('/help-requests') && c.init.method === 'POST')
    expect(posts()).toHaveLength(2); expect(posts()[0].init.body).toBe(posts()[1].init.body); expect(posts()[0].init.headers).toEqual(posts()[1].init.headers)
    expect(JSON.parse(posts()[0].init.body as string)).toMatchObject({ kind: 'appeal', attempt_id: s.a.id, expected_revision: 6 })
    // Still waiting: the same journaled request is returned, no second appeal is created.
    expect((await s.client.requestHelp(s.c.id, 'appeal', '再催一下')).id).toBe('help_one'); expect(posts()).toHaveLength(2)
    s.reads.set(`/v1/decisions/${s.c.current_decision!.id}`, () => ({ record: s.c.current_decision, current_revision: 1, currently_effective: true }))
    const record = await s.client.caseRecord(s.c.id)
    expect(record.help.map(h => [h.kind, h.status])).toEqual([['appeal', 'waiting']])
  })
  it('allows a new appeal after the teacher closed the earlier one, with a new key', async () => {
    const s = appealSetup(); await login(s.client)
    await s.client.requestHelp(s.c.id, 'appeal', '第一次').catch(() => undefined)
    await s.client.requestHelp(s.c.id, 'appeal', '第一次')
    Object.assign(s.created[0], { status: 'resolved', claimed_by: 'staff_one', claimed_at: '2026-10-16T09:00:00Z', completed_at: '2026-10-16T09:10:00Z', resolution_note: '老师已重新核对，结果不变。', revision: 3 })
    const next = await s.client.requestHelp(s.c.id, 'appeal', '第二次')
    expect(next.id).toBe('help_two')
    const posts = s.calls.filter(c => c.path.endsWith('/help-requests') && c.init.method === 'POST')
    expect((posts.at(-1)!.init.headers as Record<string, string>)['Idempotency-Key']).not.toBe((posts[0].init.headers as Record<string, string>)['Idempotency-Key'])
  })
  it('refuses an appeal when there is no current result, and a duplicate open help of the same kind', async () => {
    const s = appealSetup(); s.c.current_decision = null; s.c.progress = 'pending'; await login(s.client)
    await expect(s.client.requestHelp(s.c.id, 'appeal', '请重查')).rejects.toMatchObject({ code: 'appeal_unavailable' })
    s.c.help_request_ids = ['help_one']; s.created.push({ ...fixture<HelpRequest>('resolved_help'), id: 'help_one', case_id: s.c.id, attempt_id: s.a.id, kind: 'question', status: 'claimed', completed_at: null, resolution_note: null })
    await expect(s.client.requestHelp(s.c.id, 'question', '请老师看看')).rejects.toMatchObject({ code: 'help_already_open' })
    expect(s.calls.some(c => c.path.endsWith('/help-requests') && c.init.method === 'POST')).toBe(false)
  })
  it('reads the full case record and rejects records of another case', async () => {
    const s = harness(), { c, a } = s.addCase('case_one'), d = s.decisionFor(c, a)
    const check: CheckRun = { id: 'check_one', revision: 1, job_id: 'job_check', case_id: c.id, input_versions: d.input_versions, model_version: null, rule_version: null, prompt_version: null, decision_ids: [d.id], created_at: '2026-10-16T08:00:00Z' }
    const hint: Hint = { ...fixture<Hint>('shown_but_invalidated_hint'), id: 'hint_one', case_id: c.id, attempt_id: a.id, source_decision_id: d.id }
    const review: Review = { ...fixture<Review>('staff_review'), id: 'review_one', case_id: c.id, target_decision_id: d.id, new_decision_id: null, action: 'retract' }
    Object.assign(c, { check_ids: [check.id], hint_ids: [hint.id], review_ids: [review.id] })
    s.reads.set('/v1/checks/check_one', () => check); s.reads.set('/v1/hints/hint_one', () => hint); s.reads.set('/v1/reviews/review_one', () => review)
    s.reads.set(`/v1/decisions/${d.id}`, () => ({ record: { ...d, revision: 2, validity: 'retracted' }, current_revision: 2, currently_effective: false }))
    await login(s.client)
    const record = await s.client.caseRecord(c.id)
    expect(record.decisions[0].record.validity).toBe('retracted'); expect(record.reviews[0].action).toBe('retract'); expect(record.attempts).toHaveLength(1)
    review.case_id = 'case_foreign'
    await expect(s.client.caseRecord(c.id)).rejects.toMatchObject({ code: 'invalid_response' })
  })
})

describe('device cache hygiene', () => {
  const entry = (scope: string, id: string, updatedAt: string | undefined, response?: unknown): JournalEntry => ({ scope, id, path: '/v1/test', key: `key_${id}`, body: '{}', shape: 'Accepted', updatedAt, ...(response === undefined ? {} : { response }) })
  const draft = (scope: string, id: string, updatedAt: string): StoredDraft => ({ format: 2, id, scope, title: '合成作业', stage: 'failed', createdAt: updatedAt, updatedAt, homeworkId: null, error: null, pages: [{ id: 'page_one', photo, rotation: 0, crop: null, uploaded: false, ticketGeneration: 0 }], locked: true, detail: null, generation: 0, checkJobs: {} })
  it('purges entries older than 7 days, keeps a draft alive while its unknown write is young, and leaves one notice', async () => {
    const store = new DraftStore(new IDBFactory()), now = Date.parse('2026-10-17T00:00:00Z'), a = scopeOf(), b = scopeOf('actor_other')
    await store.saveDraft(draft(a, 'draft_old', daysAgo(10, now))); await store.saveDraft(draft(a, 'draft_kept', daysAgo(10, now))); await store.saveDraft(draft(a, 'draft_recent', daysAgo(1, now)))
    await store.reserve(entry(a, 'draft_old:homework', daysAgo(9, now), { id: 'homework_one' }))
    await store.reserve(entry(a, 'draft_kept:recognition-0', daysAgo(2, now)))
    await store.reserve(entry(a, 'hint:case_one', daysAgo(8, now)))
    await store.reserve(entry(b, 'help:case_two', daysAgo(8, now)))
    await store.reserve(entry(a, 'legacy:op', undefined, { ok: true }))
    expect(await store.purgeExpired(now)).toEqual({ drafts: 1, operations: 3, expiredPending: 2 })
    expect((await store.drafts(a)).map(d => d.id).sort()).toEqual(['draft_kept', 'draft_recent'])
    expect(await store.operation(a, 'draft_kept:recognition-0')).toBeDefined(); expect(await store.operation(a, 'hint:case_one')).toBeUndefined()
    expect(await store.takeNotice(a)).toMatchObject({ expiredPending: 1 }); expect(await store.takeNotice(a)).toBeUndefined(); expect(await store.takeNotice(b)).toMatchObject({ expiredPending: 1 })
    // Legacy entries without a timestamp start their clock at the first sweep.
    expect(await store.operation(a, 'legacy:op')).toMatchObject({ updatedAt: new Date(now).toISOString() })
    expect(await store.purgeExpired(now + LOCAL_TTL_MS + 1)).toMatchObject({ drafts: 2, operations: 2, expiredPending: 1 })
  })
  it('purges on startup before any draft read and shows the expired-write notice once after sign-in', async () => {
    const store = new DraftStore(new IDBFactory())
    await store.saveDraft(draft(scopeOf(), 'draft_stale', daysAgo(8))); await store.reserve(entry(scopeOf(), 'draft_stale:homework', daysAgo(8)))
    const s = harness(store); await login(s.client)
    expect(await s.client.listDrafts()).toEqual([])
    expect(await s.client.takeDeviceNotice()).toContain('1 条超过 7 天'); expect(await s.client.takeDeviceNotice()).toBeNull()
  })
  it('removes only this scope on access_denied, but keeps drafts on a non-contract 403 or local expiry', async () => {
    const s = harness(); await s.store.saveDraft(draft(scopeOf('actor_other'), 'draft_other', new Date().toISOString()))
    await login(s.client); await s.client.createDraft(photo.blob)
    s.setMe(() => json({}, 403)); await expect(s.client.listDrafts()).rejects.toMatchObject({ status: 403 })
    s.setMe(() => json({ actor_id: 'actor_test', roles: ['student'], authorized_students: [{ student_id: 'student_one', display_name: '合成学生', campus_id: 'campus_one' }] }))
    await login(s.client); expect(await s.client.listDrafts()).toHaveLength(1)
    const ok = s.client.identity!
    s.setMe(() => problem(403, 'access_denied')); await expect(s.client.listDrafts()).rejects.toMatchObject({ code: 'account_unavailable', status: 403 })
    expect(s.client.identity).toBeNull(); expect(s.storage.length).toBe(0)
    expect(await s.store.drafts(scopeOf())).toEqual([]); expect(await s.store.drafts(scopeOf('actor_other'))).toHaveLength(1)
    s.setMe(() => json(ok)); await login(s.client); expect(await s.client.listDrafts()).toEqual([])
  })
  it('a server 401 on a still-valid session (revoked or deleted account) purges, also during restore', async () => {
    const s = harness(); await login(s.client); await s.client.createDraft(photo.blob)
    const next = new StudentClient(s.options)
    s.setMe(() => problem(401, 'authentication_required'))
    await expect(next.restore()).rejects.toMatchObject({ code: 'account_unavailable', status: 401 })
    expect(await s.store.drafts(scopeOf())).toEqual([])
  })
  it('a locally expired session never purges device data', async () => {
    const s = harness(); await login(s.client); await s.client.createDraft(photo.blob)
    s.storage.setItem('xueban:http://localhost:session', JSON.stringify({ access_token: 'synthetic.access.token', expires_at: '2000-01-01T00:00:00Z', kind: 'account' }))
    const next = new StudentClient(s.options); expect(await next.restore()).toBeNull()
    expect(await s.store.drafts(scopeOf())).toHaveLength(1)
  })
  it('logout revokes every object URL of the scope; 清除本机草稿 removes drafts and journal', async () => {
    const s = harness(); await login(s.client); const revoke = vi.spyOn(URL, 'revokeObjectURL')
    const d = await s.client.createDraft(photo.blob); const first = s.client.objectUrl(photo.blob), second = s.client.objectUrl(photo.blob)
    await s.store.reserve(entry(scopeOf(), `${d.id}:homework`, new Date().toISOString()))
    const report = await s.client.clearLocalData()
    expect(report).toEqual({ drafts: 1, operations: 1, expiredPending: 1 }); expect(await s.client.listDrafts()).toEqual([])
    expect(revoke).toHaveBeenCalledWith(first); expect(revoke).toHaveBeenCalledWith(second)
    const third = s.client.objectUrl(photo.blob); revoke.mockClear()
    await s.client.logout(); expect(revoke).toHaveBeenCalledWith(third)
    revoke.mockRestore()
  })
})


describe('original-image recognition recovery', () => {
  function recoveryHarness() {
    const s = harness(), { c, q, a } = s.addCase('case_one')
    a.answer_text = null; a.recognition_status = 'needs_revision'; s.hw.case_ids = [c.id]
    const status: RecognitionRecoveryStatus = { case_id: c.id, attempt_id: a.id, limit: 2, used: 0, remaining: 2, active_job_id: null, last_job_id: null, last_outcome: null, eligible: true, blocked_reason: null }
    s.reads.set(`/v1/cases/${c.id}/recognition-rechecks/status`, () => status)
    const accepted = (): RecognitionRecheckAccepted => ({ ...fixture<RecognitionRecheckAccepted>('accepted_recognition_recheck'), recovery: { ...status } })
    const running = () => { Object.assign(status, { used: 1, remaining: 1, active_job_id: 'job_recheck', last_job_id: 'job_recheck', eligible: false, blocked_reason: 'active_recovery' }); return accepted() }
    return { ...s, c, q, a, status, row: { case: c, question: q, attempt: a }, running }
  }
  it('requests original-image recovery with null transcript and all four versions, without creating an attempt', async () => {
    const s = recoveryHarness(); await login(s.client)
    s.onWrite(path => path.endsWith('/recognition-rechecks') ? json(s.running(), 202) : undefined)
    const result = await s.client.startRecognitionRecheck(s.row)
    expect(result.active_job_id).toBe('job_recheck')
    const posts = s.calls.filter(c => c.init.method === 'POST' && !c.path.includes('/auth/'))
    expect(posts.map(p => p.path)).toEqual(['/v1/cases/case_one/recognition-rechecks'])
    expect(JSON.parse(String(posts[0].init.body))).toEqual({ expected_revision: s.c.revision, attempt_id: s.a.id, question_revision: s.q.revision, attempt_recognition_revision: s.a.recognition_revision })
    expect(s.c.attempt_ids).toEqual([s.a.id])
  })
  it('uses server exhaustion and restores an in-flight job after reload without POST', async () => {
    const s = recoveryHarness(); await login(s.client)
    Object.assign(s.status, { used: 2, remaining: 0, eligible: false, blocked_reason: 'budget_exhausted', last_job_id: 'job_last', last_outcome: 'unchanged_pending' })
    expect((await s.client.startRecognitionRecheck(s.row)).remaining).toBe(0)
    s.running()
    const restored = new StudentClient(s.options); await restored.restore()
    expect((await restored.recognitionRecoveryStatus(s.c.id, s.a.id)).active_job_id).toBe('job_recheck')
    expect((await restored.startRecognitionRecheck(s.row)).active_job_id).toBe('job_recheck')
    expect(s.calls.filter(c => c.init.method === 'POST' && !c.path.includes('/auth/'))).toHaveLength(0)
  })
  it.each(['network', '503'])('resolves unknown %s submission by GET and does not dispatch another request', async kind => {
    const s = recoveryHarness(); await login(s.client)
    s.onWrite(path => { if (!path.endsWith('/recognition-rechecks')) return; s.running(); if (kind === 'network') throw new Error('synthetic disconnection'); return json({ status: 503 }, 503) })
    expect((await s.client.startRecognitionRecheck(s.row)).active_job_id).toBe('job_recheck')
    expect(s.calls.filter(c => c.path.endsWith('/recognition-rechecks') && c.init.method === 'POST')).toHaveLength(1)
  })
  it('preserves the unknown original idempotency key after reload and manual retry', async () => {
    const s = recoveryHarness(); await login(s.client)
    s.onWrite(path => { if (path.endsWith('/recognition-rechecks')) throw new Error('synthetic no commit'); return undefined })
    await expect(s.client.startRecognitionRecheck(s.row)).rejects.toMatchObject({ code: 'network_unknown' })
    const restored = new StudentClient(s.options); await restored.restore()
    s.onWrite(path => path.endsWith('/recognition-rechecks') ? json(s.running(), 202) : undefined)
    await restored.startRecognitionRecheck(s.row)
    const posts = s.calls.filter(c => c.path.endsWith('/recognition-rechecks') && c.init.method === 'POST')
    expect(posts).toHaveLength(2)
    expect(posts[0].init.body).toBe(posts[1].init.body)
    expect((posts[0].init.headers as Record<string, string>)['Idempotency-Key']).toBe((posts[1].init.headers as Record<string, string>)['Idempotency-Key'])
  })
  it('does not accept another attempt, student or impossible budget in a recovery response', async () => {
    const s = recoveryHarness(); await login(s.client)
    s.status.attempt_id = 'attempt_other'
    await expect(s.client.recognitionRecoveryStatus(s.c.id, s.a.id)).rejects.toMatchObject({ code: 'stale_input' })
    s.status.attempt_id = s.a.id; s.status.remaining = 1
    await expect(s.client.recognitionRecoveryStatus(s.c.id, s.a.id)).rejects.toMatchObject({ code: 'invalid_response' })
    s.c.scope.student_id = 'student_other'
    await expect(s.client.startRecognitionRecheck(s.row)).rejects.toMatchObject({ code: 'access_denied' })
  })
  it.each(['normal', 'correction'] as const)('persists a first-read applied result into the active %s cache without altering draft identity or sending POST', async kind => {
    const s = recoveryHarness(); await login(s.client)
    const opened = await s.client.openHomework(s.hw.id)
    const cached = await s.store.draft(scopeOf(), opened.draftId!)
    cached.stage = 'complete'; cached.pages = [{ id: 'page_local', photo, rotation: 0, crop: null, uploaded: true, ticketGeneration: 2, assetId: 'asset_test' }]
    cached.generation = 3; cached.checkJobs = { [s.c.id]: 'job_original' }
    if (kind === 'correction') {
      cached.id = 'correction_local'; cached.correction = { caseId: s.c.id, parentAttemptId: 'attempt_parent', reportedHelp: 'unknown' }
    }
    await s.store.saveDraft(cached)
    const before = await s.client.getDraft(cached.id), originalRow = before.detail!.cases[0]
    expect(originalRow.attempt.answer_text).toBeNull()
    s.c.revision++; s.q.revision++; s.c.current_question_revision = s.q.revision
    s.a.answer_text = '7'; s.a.recognition_revision++
    s.c.current_decision = s.decisionFor(s.c, s.a, { verdict: 'correct' })
    s.reads.set(`/v1/questions/${s.q.question_id}`, () => ({ record: s.q, current_revision: s.q.revision, currently_effective: true }))
    s.reads.set(`/v1/attempts/${s.a.id}`, () => ({ record: s.a, current_revision: s.a.recognition_revision, currently_effective: true }))
    Object.assign(s.status, { used: 1, remaining: 1, last_job_id: 'job_recheck', last_outcome: 'applied', eligible: false, blocked_reason: 'already_resolved' })
    let refreshes = 0
    const controller = new RecognitionRecoveryController(s.client, originalRow, () => {}, async () => { refreshes++; await s.client.refreshRecognitionDraft(cached.id, s.hw.id) })
    await controller.load(); await controller.load(); controller.dispose()
    const refreshed = await s.client.getDraft(cached.id), stored = await s.store.draft(scopeOf(), cached.id)
    expect(refreshes).toBe(1); expect(refreshed.detail!.cases[0].attempt.answer_text).toBe('7')
    expect(refreshed.detail!.cases[0].case.current_decision?.verdict).toBe('correct')
    expect(refreshed.detail!.draftId).toBe(cached.id)
    expect(stored.correction).toEqual(cached.correction); expect(stored.pages).toEqual(cached.pages)
    expect(stored.generation).toBe(3); expect(stored.checkJobs).toEqual(cached.checkJobs); expect(stored.stage).toBe('complete')
    expect(s.calls.filter(c => c.init.method === 'POST' && !c.path.includes('/auth/'))).toHaveLength(0)
    // A subsequent ordinary sync/reopen reads the same persisted versions, not old null text.
    expect((await s.client.getDraft(cached.id)).detail!.cases[0].attempt.answer_text).toBe('7')
  })
  it('does not let background draft polling launch /checks while recovery is active', async () => {
    const s = recoveryHarness(); await login(s.client)
    const accepted = s.running(); s.hw.job_ids = [accepted.job.id]
    s.reads.set(`/v1/jobs/${accepted.job.id}`, () => accepted.job)
    const opened = await s.client.openHomework(s.hw.id)
    expect((await s.client.runDraft(opened.draftId!)).stage).toBe('waiting')
    expect(s.calls.filter(c => c.init.method === 'POST' && !c.path.includes('/auth/'))).toHaveLength(0)
  })
})
