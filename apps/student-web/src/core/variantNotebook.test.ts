import { describe, expect, it, vi } from 'vitest'
import { IDBFactory } from 'fake-indexeddb'
import { StudentClient, type ClientOptions } from './student'
import { DraftStore, LOCAL_TTL_MS } from './store'
import { isPracticeSession, isPracticeSubmission, isVariantNotebook, isVariantNotebookDraft, isVariantNotebookQuestion, variantNotebookStatuses, type PracticeSubmission, type VariantNotebookDraft, type VariantNotebookQuestion } from './learning'
import type { NormalizedPhoto } from './types'
import { session } from './learning.fixtures'

class MemoryStorage implements Storage {
  values = new Map<string, string>()
  get length() { return this.values.size }
  clear() { this.values.clear() }
  getItem(key: string) { return this.values.get(key) ?? null }
  key(index: number) { return [...this.values.keys()][index] ?? null }
  removeItem(key: string) { this.values.delete(key) }
  setItem(key: string, value: string) { this.values.set(key, value) }
}
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } })
const question: VariantNotebookQuestion = { case_id: 'case_one', question_id: 'question_one', question_text: '计算：1 + 1', homework_title: null, occurred_at: '2026-10-25T08:00:00Z', status: 'uncorrected', practiced: false, generation_id: null, session_id: null }
const notebook = { student_id: 'student_one', items: [question] }

it('validates optional generation progress while preserving older notebook responses', () => {
  expect(isVariantNotebookQuestion(question)).toBe(true)
  for (const status of [null, 'queued', 'generating', 'verifying', 'ready', 'failed', 'superseded']) {
    expect(isVariantNotebookQuestion({ ...question, generation_status: status })).toBe(true)
  }
  expect(isVariantNotebookQuestion({ ...question, generation_status: 'unknown' })).toBe(false)
  expect(isVariantNotebookQuestion({ ...question, generation_status: 1 })).toBe(false)
})
const generation = { job_id: 'generation_one', student_id: 'student_one', source_case_id: 'case_one', graph_version: null, knowledge_id: null, question_type: 'calculation', status: 'queued', item_id: null, failure_code: null, session_id: null, notice: '合成测试' }
const pendingAnswer = { item_id: 'item_1', result: 'pending', assessment_id: 'assessment_one', solution_text: null, session_status: 'active', feedback: '核验中' }
const submission: PracticeSubmission = { answer_text: '  2(x + 1)\n= 2x + 2  ', selected_options: [], answers: [{ field_id: 'first', answer_text: '' }, { field_id: 'second', answer_text: ' 2x + 2 ' }] }
const notebookDraft: VariantNotebookDraft = { generation_id: 'generation_one', session_id: 'practice_1', regenerate_token: 'retry-stable-token', input: { text: submission.answer_text, selected: [], fields: { first: '', second: ' 2x + 2 ' } }, photo: null }
const identity = (actor = 'actor_one', student = 'student_one') => ({ actor_id: actor, roles: ['student'], authorized_students: [{ student_id: student, display_name: '合成学生', campus_id: 'campus_one' }] })
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>(done => { resolve = done }); return { promise, resolve } }
type Route = (init: RequestInit) => Response | Promise<Response>

function harness(routes: Record<string, Route> = {}, options: Partial<ClientOptions> = {}) {
  let profile = identity(), me: (() => Response | Promise<Response>) | undefined
  const calls: { path: string; init: RequestInit }[] = []
  const storage = new MemoryStorage(), store = options.store ?? new DraftStore(new IDBFactory())
  const transport = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const path = new URL(String(input)).pathname; calls.push({ path, init })
    if (path === '/v1/auth/login') return json({ access_token: 'synthetic.access.token', expires_at: '2099-01-01T00:00:00Z', must_change_password: false })
    if (path === '/v1/auth/account') return json({ username: 'synthetic.student', student_id: profile.authorized_students[0].student_id, must_change_password: false })
    if (path === '/v1/me') return me ? me() : json(profile)
    if (path === '/v1/auth/logout') return json({ logged_out: true })
    const route = routes[`${init.method ?? 'GET'} ${path}`]
    if (!route) throw new Error(`Unexpected synthetic route ${init.method} ${path}`)
    return route(init)
  }) as typeof fetch
  const client = new StudentClient({ origin: 'http://localhost', storage, store, transport, ...options })
  return { client, calls, storage, store, setIdentity: (actor: string, student: string) => { profile = identity(actor, student) }, setMe: (next: typeof me) => { me = next }, login: () => client.login('synthetic.student', 'synthetic password') }
}

async function photo(): Promise<NormalizedPhoto> {
  const bytes = Uint8Array.from({ length: 100_000 }, (_, index) => index % 256)
  // Synthetic JPEG SOF header: 1200 x 1600. No browser decode or real photo needed.
  bytes.set([255, 216, 255, 192, 0, 17, 8, 6, 64, 4, 176])
  const blob = new Blob([bytes], { type: 'image/jpeg' }), digest = await crypto.subtle.digest('SHA-256', bytes)
  return { blob, mime: 'image/jpeg', size: bytes.length, width: 1200, height: 1600, sha256: Array.from(new Uint8Array(digest), n => n.toString(16).padStart(2, '0')).join('') }
}

describe('variant notebook contracts', () => {
  it('accepts exactly the five statuses and nullable display/link fields', () => {
    for (const status of variantNotebookStatuses) expect(isVariantNotebook({ ...notebook, items: [{ ...question, status }] })).toBe(true)
    expect(isVariantNotebookQuestion({ ...question, question_text: null, generation_id: 'generation_one', session_id: 'practice_1', practiced: true })).toBe(true)
    expect(isVariantNotebook({ student_id: 'student_one', items: [] })).toBe(true)
  })
  it('rejects missing required fields, unknown statuses, invalid types and malformed dates', () => {
    for (const key of Object.keys(question)) {
      const value = { ...question } as Record<string, unknown>; delete value[key]
      expect(isVariantNotebookQuestion(value), key).toBe(false)
    }
    for (const patch of [{ status: 'incorrect' }, { status: 'ready' }, { practiced: 'true' }, { question_text: 1 }, { homework_title: false }, { generation_id: '' }, { session_id: 4 }, { occurred_at: 'later' }, { case_id: '' }, { question_id: null }]) expect(isVariantNotebookQuestion({ ...question, ...patch })).toBe(false)
    for (const value of [null, [], {}, { items: [] }, { ...notebook, student_id: '' }, { ...notebook, items: [null] }]) expect(isVariantNotebook(value)).toBe(false)
  })
  it('validates the optional top-level no_expiry flag', () => {
    expect(isPracticeSession(session)).toBe(true)
    for (const no_expiry of [true, false]) expect(isPracticeSession({ ...session, no_expiry })).toBe(true)
    for (const no_expiry of ['true', 1, null, {}]) expect(isPracticeSession({ ...session, no_expiry })).toBe(false)
  })
  it('allows incomplete drafts but rejects malformed or excessive answers', () => {
    expect(isPracticeSubmission(submission)).toBe(true)
    expect(isPracticeSubmission({ answer_text: '', answers: [], selected_options: [] })).toBe(true)
    expect(isPracticeSubmission({ answer_text: '🙂'.repeat(4000) })).toBe(true)
    for (const value of [null, {}, { answer_text: 4 }, { answer_text: 'a'.repeat(4001) }, { answer_text: '', selected_options: ['A', 'A'] }, { answer_text: '', selected_options: ['A'.repeat(9)] }, { answer_text: '', answers: [{ field_id: 'x', answer_text: '' }, { field_id: 'x', answer_text: '' }] }]) expect(isPracticeSubmission(value)).toBe(false)
  })
})

describe('variant notebook client requests', () => {
  it('reads only the authenticated student notebook and validates its owner and shape', async () => {
    let body: unknown = notebook
    const s = harness({ 'GET /v1/students/student_one/variant-notebook': () => json(body) })
    await expect(s.client.variantNotebook()).rejects.toMatchObject({ code: 'authentication_required' })
    await s.login()
    expect(await s.client.variantNotebook()).toEqual(notebook)
    const call = s.calls.find(call => call.path.endsWith('/variant-notebook'))!
    expect(call.init.method).toBe('GET'); expect(call.init.body).toBeUndefined()
    expect(call.init.headers).toMatchObject({ Authorization: 'Bearer synthetic.access.token' })
    body = { ...notebook, student_id: 'another_student' }
    await expect(s.client.variantNotebook()).rejects.toMatchObject({ code: 'access_denied' })
    body = { ...notebook, items: [{ ...question, status: 'invented' }] }
    await expect(s.client.variantNotebook()).rejects.toMatchObject({ code: 'invalid_response' })
  })
  it('preserves an explicit regeneration token on retry and omits it for resume', async () => {
    let fail = false
    const s = harness({ 'POST /v1/students/student_one/variant-generations': () => { if (fail) { fail = false; throw new Error('Synthetic network loss') } return json(generation, 202) } })
    await s.login()
    const request = { source_case_id: 'case_one', count: 1 as const, willing: true as const }
    await s.client.generateVariant(request)
    fail = true
    await expect(s.client.generateVariant({ ...request, regenerate_token: 'retry-stable-token' })).rejects.toMatchObject({ code: 'network_unknown' })
    await s.client.generateVariant({ ...request, regenerate_token: 'retry-stable-token' })
    const sent = s.calls.filter(call => call.path.endsWith('/variant-generations'))
    expect(JSON.parse(String(sent[0].init.body))).toEqual(request)
    expect(JSON.parse(String(sent[1].init.body))).toEqual({ ...request, regenerate_token: 'retry-stable-token' })
    expect(sent[2].init.body).toBe(sent[1].init.body)
    for (const call of sent) expect(new Headers(call.init.headers).get('Idempotency-Key')).toBeTruthy()
    await expect(s.client.generateVariant({ ...request, regenerate_token: '' })).rejects.toMatchObject({ code: 'invalid_request' })
    expect(s.calls.filter(call => call.path.endsWith('/variant-generations'))).toHaveLength(3)
  })
  it('posts an empty retry request and accepts the owned replacement session', async () => {
    const original = { ...session, status: 'completed', items: [{ ...session.items[0], response: { answer_text: '4', result: 'incorrect', hint_used: false } }] }
    let result: unknown = { ...session, id: 'practice_retry', items: [session.items[0]], no_expiry: true }
    const s = harness({ 'POST /v1/practice-sessions/practice_1/retry': () => json(result) })
    await s.login()
    expect((await s.client.retryPractice(original.id)).id).toBe('practice_retry')
    expect((await s.client.retryPractice(original.id)).id).toBe('practice_retry')
    expect(original.items[0].response).toEqual({ answer_text: '4', result: 'incorrect', hint_used: false })
    for (const call of s.calls.filter(call => call.path.endsWith('/retry'))) { expect(JSON.parse(String(call.init.body))).toEqual({}); expect(new Headers(call.init.headers).get('Idempotency-Key')).toBeTruthy() }
    result = { ...session, student_id: 'other_student' }
    await expect(s.client.retryPractice('practice_1')).rejects.toMatchObject({ code: 'access_denied' })
    result = { ...session, status: 'retrying' }
    await expect(s.client.retryPractice('practice_1')).rejects.toMatchObject({ code: 'invalid_response' })
  })
  it('resumes expired no-expiry variants, while previews and ordinary variants still expire', async () => {
    const expired = { ...session, budget_ends_at: '2020-01-01T00:00:00Z' }
    const generated = { ...expired, id: 'generated_variant', no_expiry: true }
    let items = [{ ...expired, id: 'legacy', gate: { no_expiry: true } }, { ...expired, id: 'preview', purpose: 'preview', no_expiry: true }, { ...generated, id: 'completed', status: 'completed' }, { ...generated, student_id: 'other_student' }, generated]
    const s = harness({ 'GET /v1/students/student_one/practice-sessions': () => json({ items }) })
    await s.login(); expect((await s.client.activePractice())?.id).toBe('generated_variant')
    items = items.slice(0, 4); expect(await s.client.activePractice()).toBeNull()
  })
  it('rejects a notebook response from before a same-account session restore', async () => {
    const response = deferred<Response>(), started = deferred<void>()
    const s = harness({ 'GET /v1/students/student_one/variant-notebook': () => { started.resolve(); return response.promise } })
    await s.login()
    const pending = s.client.variantNotebook().catch(error => error); await started.promise
    await s.client.restore(); response.resolve(json(notebook))
    expect(await pending).toMatchObject({ code: 'session_changed' })
    expect(s.client.selectedStudentId).toBe('student_one')
  })
})

describe('durable scoped variant drafts', () => {
  it('restores input, generation receipts and Blob across IndexedDB client recreation', async () => {
    const factory = new IDBFactory(), first = harness({}, { store: new DraftStore(factory) })
    const rich = { ...notebookDraft, photo: await photo() }
    await first.login(); await first.client.saveVariantDraft('case_one', rich)
    const restored = harness({}, { store: new DraftStore(factory) }); await restored.login()
    const read = (await restored.client.variantDraft('case_one'))!
    expect(read.input).toEqual(rich.input); expect(read.generation_id).toBe(rich.generation_id); expect(read.regenerate_token).toBe(rich.regenerate_token)
    expect(read.photo!.blob).toBeInstanceOf(Blob); expect(await read.photo!.blob.arrayBuffer()).toEqual(await rich.photo.blob.arrayBuffer())
    read.input.fields.first = 'mutated'
    expect((await restored.client.variantDraft('case_one'))?.input).toEqual(notebookDraft.input)
    expect([...first.storage.values.values()].join('')).not.toContain('retry-stable-token')
    expect(await first.store.pending(JSON.stringify(['http://localhost', 'actor_one', 'student_one']), '')).toEqual([])
    await restored.client.saveVariantDraft('case_one', null); expect(await first.client.variantDraft('case_one')).toBeNull()
  })
  it('isolates origins, authenticated accounts and students even when case ids are equal', async () => {
    const store = new DraftStore(new IDBFactory()), owner = harness({}, { store })
    await owner.login(); await owner.client.saveVariantDraft('case_one', notebookDraft)
    for (const [origin, actor, student] of [['http://elsewhere.test', 'actor_one', 'student_one'], ['http://localhost', 'other_actor', 'student_one'], ['http://localhost', 'actor_one', 'other_student']]) {
      const other = harness({}, { origin, store }); other.setIdentity(actor, student); await other.login()
      expect(await other.client.variantDraft('case_one')).toBeNull(); await other.client.saveVariantDraft('case_one', { ...notebookDraft, input: { text: 'other answer', fields: {}, selected: [] } })
    }
    expect(await owner.client.variantDraft('case_one')).toEqual(notebookDraft)
  })
  it('blocks signed-out access and stale autosaves after an account or session switch', async () => {
    const s = harness(); await s.login(); await s.client.saveVariantDraft('case_one', notebookDraft)
    await s.client.logout(); expect(await s.client.variantDraft('case_one')).toBeNull()
    await expect(s.client.saveVariantDraft('case_one', null)).rejects.toMatchObject({ code: 'authentication_required' })
    s.setIdentity('other_actor', 'other_student'); await s.login()
    await expect(s.client.saveVariantDraft('case_one', notebookDraft)).rejects.toMatchObject({ code: 'session_changed' })
    expect(await s.client.variantDraft('case_one')).toBeNull(); await s.client.saveVariantDraft('case_one', { ...notebookDraft, input: { text: 'other answer', fields: {}, selected: [] } })
    s.setIdentity('actor_one', 'student_one'); await s.login(); expect(await s.client.variantDraft('case_one')).toEqual(notebookDraft)
    await s.client.restore()
    await expect(s.client.saveVariantDraft('case_one', null)).rejects.toMatchObject({ code: 'session_changed' })
    expect(await s.client.variantDraft('case_one')).toEqual(notebookDraft)
  })
  it('rejects malformed drafts and surfaces store errors without destroying a valid draft', async () => {
    const s = harness(); await s.login(); await s.client.saveVariantDraft('case_one', notebookDraft)
    expect(isVariantNotebookDraft(notebookDraft)).toBe(true)
    for (const invalid of [{ ...notebookDraft, regenerate_token: '' }, { ...notebookDraft, input: { ...notebookDraft.input, selected: ['A', 'A'] } }, { ...notebookDraft, photo: { ...await photo(), size: 1 } }]) {
      expect(isVariantNotebookDraft(invalid)).toBe(false)
      await expect(s.client.saveVariantDraft('case_one', invalid)).rejects.toMatchObject({ code: 'invalid_request' })
    }
    const write = vi.spyOn(s.store, 'saveOperation').mockRejectedValue(new Error('Synthetic quota limit'))
    await expect(s.client.saveVariantDraft('case_one', notebookDraft)).rejects.toThrow('Synthetic quota limit')
    write.mockRestore(); expect(await s.client.variantDraft('case_one')).toEqual(notebookDraft)
    const scope = JSON.stringify(['http://localhost', 'actor_one', 'student_one']), id = 'variant-notebook-draft:case_one'
    const entry = (await s.store.operation(scope, id))!
    await s.store.saveOperation({ ...entry, response: { input: false } }); expect(await s.client.variantDraft('case_one')).toBeNull()
  })
  it('expires drafts using the existing local retention window', async () => {
    let now = Date.now(); const s = harness({}, { now: () => now }); await s.login()
    await s.client.saveVariantDraft('case_one', notebookDraft); now += LOCAL_TTL_MS
    expect(await s.client.variantDraft('case_one')).toEqual(notebookDraft)
    now += 1000; expect(await s.client.variantDraft('case_one')).toBeNull()
    expect((await s.store.purgeExpired(now)).operations).toBe(1)
  })
  it('clears only the current account drafts with clearLocalData', async () => {
    const store = new DraftStore(new IDBFactory()), s = harness({}, { store }), other = harness({}, { store })
    await s.login(); await s.client.saveVariantDraft('case_one', notebookDraft)
    other.setIdentity('other_actor', 'student_one'); await other.login(); await other.client.saveVariantDraft('case_one', notebookDraft)
    expect((await s.client.clearLocalData()).operations).toBe(1)
    expect(await s.client.variantDraft('case_one')).toBeNull(); expect(await other.client.variantDraft('case_one')).toEqual(notebookDraft)
  })
  it('purges owned durable drafts when the server disables the account', async () => {
    const s = harness(); await s.login(); await s.client.saveVariantDraft('case_one', notebookDraft)
    s.setMe(() => json({ type: 'urn:xueban:auth', title: 'Synthetic', status: 403, category: 'authorization', code: 'access_denied', detail: 'Synthetic', request_id: 'request_one', current_revision: null, retry_after_seconds: null }, 403))
    await expect(s.client.variantNotebook()).rejects.toMatchObject({ code: 'account_unavailable' })
    expect(await s.store.operation(JSON.stringify(['http://localhost', 'actor_one', 'student_one']), 'variant-notebook-draft:case_one')).toBeUndefined()
    expect(await s.client.variantDraft('case_one')).toBeNull()
  })
  it('snapshots and serializes rapid autosaves and clearing', async () => {
    const s = harness(); await s.login()
    const first = { ...notebookDraft, input: { ...notebookDraft.input, fields: { first: 'original' } } }
    const a = s.client.saveVariantDraft('case_one', first); first.input.fields.first = 'mutation after save'
    await a; expect((await s.client.variantDraft('case_one'))?.input.fields.first).toBe('original')
    const b = s.client.saveVariantDraft('case_one', notebookDraft), c = s.client.saveVariantDraft('case_one', null)
    await Promise.all([b, c]); expect(await s.client.variantDraft('case_one')).toBeNull()
  })
  it('does not return an old draft after the session changes during the database read', async () => {
    const s = harness(); await s.login(); await s.client.saveVariantDraft('case_one', notebookDraft)
    const stored = (await s.store.operation(JSON.stringify(['http://localhost', 'actor_one', 'student_one']), 'variant-notebook-draft:case_one'))!
    const deferredRead = deferred<typeof stored>(), started = deferred<void>()
    vi.spyOn(s.store, 'operation').mockImplementationOnce(() => { started.resolve(); return deferredRead.promise })
    const reading = s.client.variantDraft('case_one').catch(error => error); await started.promise
    await s.client.restore(); deferredRead.resolve(stored)
    expect(await reading).toMatchObject({ code: 'session_changed' })
  })
})

describe('practice photo responses', () => {
  it('normalizes with the existing helper and submits its bytes and metadata as JSON', async () => {
    const normalized = await photo(), normalize = vi.fn(async () => normalized)
    const s = harness({ 'GET /v1/practice-sessions/practice_1': () => json(session), 'POST /v1/practice-sessions/practice_1/photo-responses': () => json(pendingAnswer, 202) }, { normalize })
    await s.login(); const file = new File(['synthetic original'], 'work.png', { type: 'image/png' })
    expect(await s.client.answerPracticePhoto('practice_1', 'item_1', file)).toEqual(pendingAnswer)
    expect(normalize).toHaveBeenCalledWith(file)
    const sent = s.calls.find(call => call.path.endsWith('/photo-responses'))!, body = JSON.parse(String(sent.init.body))
    expect(body).toEqual({ item_id: 'item_1', image: { data_base64: expect.any(String), mime_type: normalized.mime, width: normalized.width, height: normalized.height, sha256: normalized.sha256, byte_size: normalized.size } })
    expect(Uint8Array.from(atob(body.image.data_base64), c => c.charCodeAt(0))).toEqual(new Uint8Array(await normalized.blob.arrayBuffer()))
    expect(new Headers(sent.init.headers).get('Content-Type')).toBe('application/json')
    expect(new Headers(sent.init.headers).get('Idempotency-Key')).toBeTruthy()
    expect(sent.init.credentials).toBe('omit'); expect(sent.init.redirect).toBe('error')
  })
  it('accepts a persisted normalized photo without re-encoding and verifies its metadata', async () => {
    const normalized = await photo(), normalize = vi.fn(async () => normalized)
    const s = harness({ 'GET /v1/practice-sessions/practice_1': () => json(session), 'POST /v1/practice-sessions/practice_1/photo-responses': () => json(pendingAnswer, 202) }, { normalize })
    await s.login()
    expect(await s.client.answerPracticePhoto('practice_1', 'item_1', normalized)).toEqual(pendingAnswer)
    expect(normalize).not.toHaveBeenCalled()
    for (const change of [{ width: 1199 }, { sha256: 'a'.repeat(64) }, { size: 999 }, { blob: new Blob(['bad'], { type: 'image/jpeg' }), size: 3 }]) {
      await expect(s.client.answerPracticePhoto('practice_1', 'item_1', { ...normalized, ...change })).rejects.toMatchObject({ code: 'invalid_photo' })
    }
    expect(s.calls.filter(call => call.path.endsWith('/photo-responses'))).toHaveLength(1)
  })
  it('rejects foreign student/session/items before normalizing or posting', async () => {
    let body: unknown = { ...session, student_id: 'other_student' }
    const normalize = vi.fn(async () => photo())
    const s = harness({ 'GET /v1/practice-sessions/practice_1': () => json(body) }, { normalize })
    await s.login(); const file = new File(['synthetic'], 'work.jpg')
    await expect(s.client.answerPracticePhoto('practice_1', 'item_1', file)).rejects.toMatchObject({ code: 'access_denied' })
    body = { ...session, id: 'other_session' }
    await expect(s.client.answerPracticePhoto('practice_1', 'item_1', file)).rejects.toMatchObject({ code: 'invalid_response' })
    body = session
    await expect(s.client.answerPracticePhoto('practice_1', 'other_item', file)).rejects.toMatchObject({ code: 'access_denied' })
    expect(normalize).not.toHaveBeenCalled(); expect(s.calls.some(call => call.path.endsWith('/photo-responses'))).toBe(false)
  })
  it.each(['account switch', 'same-account restore'])('never posts old photo bytes after %s during normalization', async mode => {
    const normalized = deferred<NormalizedPhoto>(), entered = deferred<void>()
    const s = harness({ 'GET /v1/practice-sessions/practice_1': () => json(session) }, { normalize: () => { entered.resolve(); return normalized.promise } })
    await s.login(); const pending = s.client.answerPracticePhoto('practice_1', 'item_1', new File(['synthetic'], 'work.jpg')).catch(error => error)
    await entered.promise
    if (mode === 'account switch') { s.setIdentity('other_actor', 'other_student'); await s.login() } else await s.client.restore()
    normalized.resolve(await photo())
    expect(await pending).toMatchObject({ code: 'session_changed' })
    expect(s.calls.some(call => call.path.endsWith('/photo-responses'))).toBe(false)
  })
  it('validates the returned answer item and does not reinterpret a receipt as correctness', async () => {
    const s = harness({ 'GET /v1/practice-sessions/practice_1': () => json(session), 'POST /v1/practice-sessions/practice_1/photo-responses': () => json({ ...pendingAnswer, item_id: 'other_item' }, 202) }, { normalize: photo })
    await s.login()
    await expect(s.client.answerPracticePhoto('practice_1', 'item_1', new File(['synthetic'], 'work.jpg'))).rejects.toMatchObject({ code: 'invalid_response' })
  })
})
