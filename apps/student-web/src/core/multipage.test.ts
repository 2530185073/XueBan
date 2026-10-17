import { describe, expect, it, vi } from 'vitest'
import { IDBFactory } from 'fake-indexeddb'
import examples from '../../../../contracts/phase1/examples.json'
import { StudentClient } from './student'
import { DraftStore, LEGACY_PAGE_ID } from './store'
import { StudentError } from './errors'
import type { HomeworkSession, ImageAsset, Job, LegacyDraft, NormalizedPhoto, StoredDraft } from './types'

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
const json = (value: unknown, status = 201) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } })
const now = '2026-10-17T00:00:00Z'
const conflict = (code: string, revision: number) => json({ type: 'urn:xueban:conflict', title: '合成冲突', status: 409, category: 'conflict', code, detail: '合成', request_id: 'request_test', current_revision: revision, retry_after_seconds: null }, 409)
const page = (label: string, size = label.length) => new Blob([label.padEnd(size, '.')], { type: 'image/jpeg' })
const scopeOf = (actor: string, student: string) => JSON.stringify(['http://localhost', actor, student])
const students = { student_one: { actor: 'actor_one', name: '合成学生一' }, student_two: { actor: 'actor_two', name: '合成学生二' } }
type StudentKey = keyof typeof students

/** A synthetic server that keeps every homework, asset and idempotent reply by key. */
function server() {
  const storage = new MemorySession(), store = new DraftStore(new IDBFactory())
  let who: StudentKey = 'student_one', count = 0, failPut = 0, loseRecognition = false, rejectNext = false
  const homeworks = new Map<string, HomeworkSession>(), assets = new Map<string, ImageAsset>(), jobs = new Map<string, Job>(), replies = new Map<string, Response>()
  const posts: { path: string; key: string; body: Record<string, unknown> }[] = [], puts: string[] = []
  const transport = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const path = new URL(String(input)).pathname, method = init.method ?? 'GET', key = (init.headers as Record<string, string> | undefined)?.['Idempotency-Key'] ?? ''
    if (path === '/v1/auth/login') return json({ access_token: `token.${who}`, expires_at: '2099-01-01T00:00:00Z', must_change_password: false }, 200)
    if (path === '/v1/auth/logout') return json({ logged_out: true }, 200)
    if (path === '/v1/auth/account') return json({ username: `synthetic.${who}`, student_id: who, must_change_password: false }, 200)
    if (path === '/v1/me') return json({ actor_id: students[who].actor, roles: ['student'], authorized_students: [{ student_id: who, display_name: students[who].name, campus_id: 'campus_one' }] }, 200)
    if (method === 'POST') {
      const body = JSON.parse(init.body as string) as Record<string, unknown>; posts.push({ path, key, body })
      const replay = replies.get(key); if (replay) return replay.clone()
      let reply: Response
      const hwMatch = path.match(/^\/v1\/homeworks\/([^/]+)\/(uploads|recognitions)$/), assetMatch = path.match(/^\/v1\/assets\/([^/]+)\/complete$/), renewMatch = path.match(/^\/v1\/assets\/([^/]+)\/upload-ticket$/)
      if (renewMatch) { const asset = assets.get(renewMatch[1])!; asset.revision++; reply = json({ asset, put_url: `http://localhost/v1/assets/${asset.id}/content`, expires_at: '2099-01-01T00:00:00Z', required_headers: { 'X-Upload-Token': 'synthetic-renewed', 'Content-Type': 'image/jpeg' } }); replies.set(key, reply.clone()); return reply }
      if (/^\/v1\/students\/[^/]+\/homeworks$/.test(path)) {
        const hw: HomeworkSession = { ...fixture<HomeworkSession>('homework'), id: `homework_${++count}`, revision: 1, scope: { tenant_id: 'tenant_one', campus_id: 'campus_one', student_id: who }, asset_ids: [], question_ids: [], case_ids: [], job_ids: [], created_at: now }
        homeworks.set(hw.id, hw); reply = json(hw)
      } else if (hwMatch?.[2] === 'uploads') {
        const hw = homeworks.get(hwMatch[1])!
        if (body.expected_revision !== hw.revision) return conflict('revision_conflict', hw.revision)
        const asset: ImageAsset = { ...fixture<ImageAsset>('asset'), id: `asset_${++count}`, homework_id: hw.id, status: rejectNext ? 'rejected' : 'awaiting_upload', revision: 1, page_index: body.page_index as number, sha256: body.sha256 as string, byte_size: body.byte_size as number, mime_type: 'image/jpeg', width: body.width as number, height: body.height as number, created_at: new Date(Date.parse(now) + count * 1000).toISOString() }
        rejectNext = false; assets.set(asset.id, asset); hw.asset_ids = [...hw.asset_ids, asset.id].sort(); hw.revision++
        reply = json({ asset, put_url: `http://localhost/v1/assets/${asset.id}/content`, expires_at: '2099-01-01T00:00:00Z', required_headers: { 'X-Upload-Token': 'synthetic-ticket', 'Content-Type': 'image/jpeg' } })
      } else if (assetMatch) {
        const asset = assets.get(assetMatch[1])!; asset.status = 'uploaded'; asset.revision++; homeworks.get(asset.homework_id)!.revision++; reply = json(asset, 200)
      } else if (hwMatch?.[2] === 'recognitions') {
        const hw = homeworks.get(hwMatch[1])!
        if (body.expected_revision !== hw.revision) return conflict('revision_conflict', hw.revision)
        const job: Job = { id: `job_${++count}`, revision: 1, kind: 'recognize_homework', status: 'succeeded', created_at: new Date(Date.parse(now) + count * 1000).toISOString(), updated_at: now, retry_after_seconds: null, failure: null, result: { resource_type: 'homework', resource_id: hw.id } }
        jobs.set(job.id, job); hw.job_ids = [...hw.job_ids, job.id]; hw.revision++; reply = json({ job, created_resource: null }, 202)
        if (loseRecognition) { loseRecognition = false; replies.set(key, reply.clone()); throw new TypeError('synthetic lost recognition reply') }
      } else throw new Error(`Unexpected synthetic POST ${path}`)
      replies.set(key, reply.clone()); return reply
    }
    if (method === 'PUT') { puts.push(path); if (failPut && puts.length === failPut) throw new TypeError('synthetic weak network'); return new Response(null, { status: 204 }) }
    const [, kind, id] = path.split('/').slice(1)
    const found = kind === 'homeworks' ? homeworks.get(id) : kind === 'assets' ? assets.get(id) : kind === 'jobs' ? jobs.get(id) : undefined
    if (found) return json(found, 200)
    throw new Error(`Unexpected synthetic GET ${path}`)
  }) as unknown as typeof fetch
  let shaCount = 0
  const normalize = async (file: Blob, maxBytes = 4_194_304): Promise<NormalizedPhoto> => {
    if (file.size > maxBytes) throw new StudentError('page_too_large', 422, '这张照片压缩后仍然太大。')
    return { blob: file, width: 30, height: 20, sha256: (++shaCount).toString(16).padStart(64, '0'), mime: 'image/jpeg', size: file.size }
  }
  const edit = async (source: NormalizedPhoto, change: { rotation: number }): Promise<NormalizedPhoto> => ({ ...source, width: change.rotation % 180 ? source.height : source.width, height: change.rotation % 180 ? source.width : source.height, sha256: (++shaCount).toString(16).padStart(64, 'e') })
  const options = { origin: 'http://localhost', storage, store, transport, normalize, edit, sleep: async () => undefined }
  return {
    options, store, storage, homeworks, assets, posts, puts, client: new StudentClient(options),
    as: (student: StudentKey) => { who = student }, failPut: (nth: number) => { failPut = nth }, loseRecognition: () => { loseRecognition = true }, rejectNext: () => { rejectNext = true },
    uploads: () => posts.filter(p => p.path.endsWith('/uploads')), recognitions: () => posts.filter(p => p.path.endsWith('/recognitions')),
  }
}
const login = (client: StudentClient) => client.login('synthetic.student', 'synthetic password only')

describe('multi-page drafts', () => {
  it('imports several pages locally, reorders, turns, deletes and resets, without sending anything', async () => {
    const s = server(); await login(s.client)
    const d = await s.client.createDraft([page('one'), page('two'), page('three')])
    expect(d.pages).toHaveLength(3); expect(d.editable).toBe(true); expect(d.stage).toBe('local'); expect(d.pageCount).toBe(3); expect(d.uploadedCount).toBe(0)
    const [a, b, c] = d.pages.map(p => p.id)
    expect((await s.client.movePage(d.id, c, -2))!.pages.map(p => p.id)).toEqual([c, a, b])
    const turned = (await s.client.editPage(d.id, a, { rotate: 1 }))!, page1 = turned.pages.find(p => p.id === a)!
    expect([page1.width, page1.height]).toEqual([20, 30]); expect(page1.edited).toBe(true)
    const stored = await s.store.draft(scopeOf('actor_one', 'student_one'), d.id)
    expect(stored.pages.find(p => p.id === a)).toMatchObject({ rotation: 90, original: { width: 30, height: 20 } })
    const reset = (await s.client.editPage(d.id, a, { reset: true }))!.pages.find(p => p.id === a)!
    expect([reset.width, reset.height, reset.edited]).toEqual([30, 20, false])
    expect((await s.client.removePage(d.id, b))!.pages.map(p => p.id)).toEqual([c, a])
    expect(s.posts).toHaveLength(0)
    await s.client.removePage(d.id, c); expect(await s.client.removePage(d.id, a)).toBeNull()
    expect(await s.client.listDrafts()).toEqual([])
  })
  it('enforces 4 pages and the 8 MiB batch before upload, all-or-nothing per selection', async () => {
    const s = server(); await login(s.client)
    await expect(s.client.createDraft(Array.from({ length: 5 }, (_, i) => page(`p${i}`)))).rejects.toMatchObject({ code: 'too_many_pages' })
    const d = await s.client.createDraft([page('a', 3_000_000), page('b', 3_000_000)])
    await expect(s.client.addPages(d.id, [page('c'), page('d'), page('e')])).rejects.toMatchObject({ code: 'too_many_pages' })
    await expect(s.client.addPages(d.id, [page('c', 2_000), page('d', 2_500_000)])).rejects.toMatchObject({ code: 'page_too_large', message: expect.stringContaining('第 2 张') })
    expect((await s.client.getDraft(d.id)).pages).toHaveLength(2)
    await expect(s.client.createDraft([page('x', 4_194_305)])).rejects.toMatchObject({ code: 'page_too_large' })
    expect((await s.client.addPages(d.id, [page('c', 1_000_000)]))!.pages).toHaveLength(3)
    expect(await s.client.listDrafts()).toHaveLength(1); expect(s.posts).toHaveLength(0)
  })
  it('uploads pages in the chosen order, one recognition for all pages, then locks the pages', async () => {
    const s = server(); await login(s.client)
    const d = await s.client.createDraft([page('one'), page('two'), page('three')]), [a, b, c] = d.pages.map(p => p.id)
    await s.client.movePage(d.id, c, -2)
    const done = await s.client.runDraft(d.id)
    expect(done.stage).toBe('complete'); expect(done.editable).toBe(false); expect(done.uploadedCount).toBe(3)
    expect(s.posts.filter(p => p.path.endsWith('/homeworks'))).toHaveLength(1)
    expect(s.uploads().map(p => p.body.page_index)).toEqual([0, 1, 2])
    const order = done.pages.map(p => p.id); expect(order).toEqual([c, a, b])
    const [recognition] = s.recognitions(); expect(s.recognitions()).toHaveLength(1)
    expect(recognition.body.asset_ids).toEqual(done.pages.map(p => p.assetId))
    expect((recognition.body.asset_ids as string[]).map(id => s.assets.get(id)!.page_index)).toEqual([0, 1, 2])
    await expect(s.client.editPage(d.id, b, { rotate: 1 })).rejects.toMatchObject({ code: 'draft_locked' })
    await expect(s.client.discardDraft(d.id)).rejects.toMatchObject({ code: 'draft_locked' })
  })
  it('resumes after a weak-network failure mid-batch without re-sending received pages', async () => {
    const s = server(); await login(s.client); s.failPut(2)
    const d = await s.client.createDraft([page('one'), page('two'), page('three')])
    const failed = await s.client.runDraft(d.id)
    expect(failed.stage).toBe('failed'); expect(failed.uploadedCount).toBe(1); expect(failed.editable).toBe(false)
    const reopened = new StudentClient(s.options); await reopened.restore()
    expect(await reopened.listDrafts()).toMatchObject([{ id: d.id, pageCount: 3, uploadedCount: 1 }])
    expect((await reopened.runDraft(d.id, { retryFailed: true })).stage).toBe('complete')
    expect(s.uploads()).toHaveLength(3); expect(s.puts).toHaveLength(4); expect(s.puts[2]).toBe(s.puts[1]); expect(new Set(s.puts).size).toBe(3)
    expect(new Set(s.uploads().map(p => p.key)).size).toBe(3); expect(s.recognitions()).toHaveLength(1)
  })
  it('a retry after reload reuses the idempotency key of an unconfirmed recognition: one homework, one job', async () => {
    const s = server(); await login(s.client); s.loseRecognition()
    const d = await s.client.createDraft([page('one'), page('two')])
    expect((await s.client.runDraft(d.id)).stage).toBe('failed')
    const reopened = new StudentClient(s.options); await reopened.restore()
    expect((await reopened.runDraft(d.id, { retryFailed: true })).stage).toBe('complete')
    const [first, second] = s.recognitions(); expect(s.recognitions()).toHaveLength(2)
    expect(second.key).toBe(first.key); expect(second.body).toEqual(first.body)
    expect(s.posts.filter(p => p.path.endsWith('/homeworks'))).toHaveLength(1); expect([...s.homeworks.values()][0].job_ids).toHaveLength(1)
  })
  it('a page the server refused can be chosen again; only that page is re-sent, with a new key', async () => {
    const s = server(); await login(s.client)
    const d = await s.client.createDraft([page('one'), page('two')]); s.rejectNext()
    const failed = await s.client.runDraft(d.id)
    expect(failed.error?.code).toBe('image_rejected'); expect(failed.error?.message).toContain('第 1 页')
    const replaced = await s.client.replacePage(d.id, failed.pages[0].id, page('one again'))
    expect(replaced.pages[0].id).not.toBe(failed.pages[0].id); expect(replaced.pages[1].id).toBe(failed.pages[1].id); expect(replaced.error).toBeNull()
    const done = await s.client.runDraft(d.id, { retryFailed: true }); expect(done.stage).toBe('complete')
    const uploads = s.uploads(); expect(uploads).toHaveLength(3); expect(uploads[1].key).not.toBe(uploads[0].key); expect(uploads.map(u => u.body.page_index)).toEqual([0, 0, 1])
    expect(s.recognitions()[0].body.asset_ids).toEqual(done.pages.map(p => p.assetId)); expect(done.pages.map(p => p.assetId)).not.toContain(failed.pages[0].assetId)
    await expect(s.client.replacePage(d.id, replaced.pages[1].id, page('late'))).rejects.toMatchObject({ code: 'page_uploaded' })
  })
})

describe('history of a multi-page homework', () => {
  it('lists every original page in page order, leaving out a later correction photo', async () => {
    const s = server(); await login(s.client)
    const d = await s.client.createDraft([page('one'), page('two'), page('three')]), [a, b, c] = d.pages.map(p => p.id)
    await s.client.movePage(d.id, a, 2); await s.client.runDraft(d.id)
    const hw = [...s.homeworks.values()][0]
    const later: ImageAsset = { ...s.assets.get(hw.asset_ids[0])!, id: 'asset_correction', page_index: 3, status: 'uploaded', created_at: '2026-10-18T00:00:00Z' }
    s.assets.set(later.id, later); hw.asset_ids = [...hw.asset_ids, later.id].sort(); hw.revision++
    const reopened = new StudentClient(s.options); await reopened.restore()
    const detail = await reopened.openHomework(hw.id), pages = await reopened.homeworkPages(detail)
    const byLocal = new Map((await reopened.getDraft(d.id)).pages.map(p => [p.id, p.assetId]))
    expect(pages).toEqual([b, c, a].map(id => byLocal.get(id)))
    expect(pages).not.toContain('asset_correction')
  })
  it('opening a homework whose pages are not all received never marks it complete', async () => {
    const s = server(); await login(s.client); s.failPut(2)
    const d = await s.client.createDraft([page('one'), page('two')]); await s.client.runDraft(d.id)
    const hw = [...s.homeworks.values()][0]
    await s.client.openHomework(hw.id)
    expect(await s.client.getDraft(d.id)).toMatchObject({ stage: 'failed', uploadedCount: 1, pageCount: 2 })
  })
})

describe('draft migration', () => {
  it('opens a format-1 single-photo draft and replays its unconfirmed upload with the original key', async () => {
    const s = server(); await login(s.client)
    const scope = scopeOf('actor_one', 'student_one'), photo = await s.options.normalize(page('legacy'))
    const legacy: LegacyDraft = { id: 'draft_legacy', scope, title: '旧草稿', stage: 'failed', createdAt: now, updatedAt: new Date().toISOString(), homeworkId: null, error: { code: 'network_unknown', message: '合成' }, photo, detail: null, generation: 0, ticketGeneration: 0, checkJobs: {} }
    await s.store.saveDraft(legacy as unknown as StoredDraft)
    const view = await s.client.getDraft('draft_legacy')
    expect(view.pages).toHaveLength(1); expect(view.pages[0]).toMatchObject({ id: LEGACY_PAGE_ID, uploaded: false }); expect(view.photo).toBeInstanceOf(Blob); expect(view.editable).toBe(false)
    expect(await s.client.listDrafts()).toMatchObject([{ id: 'draft_legacy', pageCount: 1, uploadedCount: 0 }])
    const done = await s.client.runDraft('draft_legacy', { retryFailed: true })
    expect(done.stage).toBe('complete')
    const op = await s.store.operation(scope, 'draft_legacy:upload'); expect(op?.response).toBeDefined()
    expect(s.uploads()[0].key).toBe(op!.key)
    expect((await s.store.draft(scope, 'draft_legacy')).format).toBe(2)
  })
  it('keeps the journal name of a legacy upload so an earlier unknown write is replayed, not duplicated', async () => {
    const s = server(); await login(s.client)
    const scope = scopeOf('actor_one', 'student_one'), photo = await s.options.normalize(page('legacy'))
    const pre = await s.client.createDraft(page('warmup')); await s.client.runDraft(pre.id)
    const hw = [...s.homeworks.values()][0], body = JSON.stringify({ expected_revision: hw.revision, page_index: 0, sha256: photo.sha256, byte_size: photo.size, mime_type: 'image/jpeg', width: photo.width, height: photo.height })
    await s.store.reserve({ scope, id: 'draft_old:upload', path: `/v1/homeworks/${hw.id}/uploads`, key: 'legacy-key', body, shape: 'UploadTicket', updatedAt: new Date().toISOString() })
    const legacy: LegacyDraft = { id: 'draft_old', scope, title: '旧草稿', stage: 'uploading', createdAt: now, updatedAt: new Date().toISOString(), homeworkId: hw.id, error: null, photo, detail: null, generation: 0, ticketGeneration: 0, checkJobs: {} }
    await s.store.saveDraft(legacy as unknown as StoredDraft)
    await s.client.runDraft('draft_old')
    const legacyUploads = s.uploads().filter(p => p.key === 'legacy-key'); expect(legacyUploads).toHaveLength(1)
    expect(s.uploads().filter(p => p.path === `/v1/homeworks/${hw.id}/uploads`)).toHaveLength(2)
  })
  it('migrates a remote (server-only) format-1 draft to zero local pages', async () => {
    const s = server(); await login(s.client)
    const scope = scopeOf('actor_one', 'student_one')
    await s.store.saveDraft({ id: 'remote_homework_x', scope, title: '历史', stage: 'complete', createdAt: now, updatedAt: new Date().toISOString(), homeworkId: 'homework_x', error: null, photo: null, detail: null, generation: 0, ticketGeneration: 0, checkJobs: {} } as unknown as StoredDraft)
    expect(await s.client.getDraft('remote_homework_x')).toMatchObject({ pages: [], photo: null, editable: false, pageCount: 0 })
  })
})

describe('shared device', () => {
  it('another student on the same browser never sees, opens or changes the first student\'s drafts', async () => {
    const s = server(); await login(s.client)
    const mine = await s.client.createDraft([page('one'), page('two')])
    await s.client.logout(); s.as('student_two'); await login(s.client)
    expect(s.client.selectedStudentId).toBe('student_two')
    expect(await s.client.listDrafts()).toEqual([])
    await expect(s.client.getDraft(mine.id)).rejects.toMatchObject({ code: 'draft_not_found' })
    await expect(s.client.addPages(mine.id, [page('x')])).rejects.toMatchObject({ code: 'draft_not_found' })
    await expect(s.client.discardDraft(mine.id)).rejects.toMatchObject({ code: 'draft_not_found' })
    const theirs = await s.client.createDraft(page('theirs'))
    await s.client.logout(); s.as('student_one'); await login(s.client)
    expect((await s.client.listDrafts()).map(d => d.id)).toEqual([mine.id])
    expect((await s.client.getDraft(mine.id)).pages).toHaveLength(2)
    await expect(s.client.getDraft(theirs.id)).rejects.toMatchObject({ code: 'draft_not_found' })
  })
  it('a local draft that has sent nothing can be discarded; a submitted one cannot', async () => {
    const s = server(); await login(s.client)
    const keep = await s.client.createDraft(page('keep')), drop = await s.client.createDraft(page('drop'))
    await s.client.discardDraft(drop.id); expect((await s.client.listDrafts()).map(d => d.id)).toEqual([keep.id])
  })
})
