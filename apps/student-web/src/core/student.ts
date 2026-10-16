import type { Accepted, AssetAccess, AttemptRead, CaseDetail, CheckRun, CorrectionCase, CreateCorrection, CreateHelp, DraftView, HelpRequest, Hint, HomeworkDetail, HomeworkPage, HomeworkSession, IdentityContext, ImageAsset, InputVersions, Job, JournalEntry, NormalizedPhoto, QuestionRead, RequestHint, StoredDraft, UploadTicket } from './types'
import { StudentApi } from './api'
import { StudentError, safeError } from './errors'
import { DraftStore } from './store'
import { normalizePhoto } from './photo'
import { valid } from './validation'

export const remoteDraftId = (id: string) => `remote_${id}`
const encoded = encodeURIComponent
const active = (job: Job) => ['queued', 'running', 'retryable_failed'].includes(job.status)
const matches = (v: InputVersions, row: CaseDetail) => v.question_id === row.case.question_id && v.question_revision === row.case.current_question_revision && v.attempt_id === row.case.current_attempt_id && v.attempt_recognition_revision === row.attempt.recognition_revision
export function effectiveDecision(row: CaseDetail) { const d = row.case.current_decision; return d && !['stale', 'retracted'].includes(d.validity) && matches(d.input_versions, row) ? d : null }
export function decisionLabel(row: CaseDetail): string {
  const d = effectiveDecision(row)
  if (row.attempt.recognition_status !== 'ready') return '识别待核实 · 请重拍或请老师看'
  if (!d) return '等待检查'
  if (d.validity === 'needs_review' || d.verdict === 'unable_to_judge') return '暂时看不准 · 请老师一起看'
  const text = { correct: '正确', incorrect: '需要订正', partially_correct: '部分需要订正', unanswered: '尚未作答' }[d.verdict]
  return `${d.validity === 'confirmed' && d.origin === 'staff' ? '老师确认' : 'AI 初判'} · ${text}`
}
interface Context { scope: string; student: string; epoch: number }
interface Session { access_token: string; expires_at: string }
interface PendingPair { code: string; key: string }
export interface ClientOptions { origin?: string; transport?: typeof fetch; storage?: Storage; store?: DraftStore; normalize?: (file: Blob) => Promise<NormalizedPhoto>; sleep?: (ms: number) => Promise<void> }
export class StudentClient {
  private session: Session | null = null
  private currentIdentity: IdentityContext | null = null
  private student: string | null = null
  private epoch = 0
  private listeners = new Set<() => void>()
  private api: StudentApi
  private store: DraftStore
  private storage: Storage
  private origin: string
  private normalize: (file: Blob) => Promise<NormalizedPhoto>
  private sleep: (ms: number) => Promise<void>
  private runs = new Map<string, Promise<DraftView>>()
  private images = new Map<string, Promise<Blob>>()
  constructor(options: ClientOptions = {}) {
    this.origin = options.origin ?? location.origin
    const url = new URL(this.origin)
    if (url.origin !== this.origin || !['http:', 'https:'].includes(url.protocol)) throw new StudentError('invalid_origin', 422)
    this.storage = options.storage ?? sessionStorage; this.store = options.store ?? new DraftStore()
    this.normalize = options.normalize ?? normalizePhoto; this.sleep = options.sleep ?? (ms => new Promise(resolve => setTimeout(resolve, ms)))
    this.api = new StudentApi(this.origin, () => this.session?.access_token ?? null, () => this.disconnect(), options.transport)
  }
  get identity() { return this.currentIdentity }
  get selectedStudentId() { return this.student }
  subscribe(listener: () => void) { this.listeners.add(listener); return () => { this.listeners.delete(listener) } }
  private changed() { for (const listener of this.listeners) listener() }
  private storageKey(name: string) { return `xueban:${this.origin}:${name}` }
  private load<T>(name: string): T | null { try { return JSON.parse(this.storage.getItem(this.storageKey(name)) ?? 'null') as T | null } catch { return null } }
  private persist(name: string, value: unknown) { try { this.storage.setItem(this.storageKey(name), JSON.stringify(value)) } catch { throw new StudentError('session_storage_unavailable', 0, '无法保存本次会话，请允许浏览器会话存储后重试。') } }
  disconnect() { this.epoch++; this.session = null; this.currentIdentity = null; this.student = null; this.images.clear(); this.storage.removeItem(this.storageKey('session')); this.storage.removeItem(this.storageKey('selected')); this.storage.removeItem(this.storageKey('pair')); this.changed() }
  selectStudent(id: string) { if (!this.currentIdentity?.authorized_students.some(s => s.student_id === id)) throw new StudentError('access_denied', 403); this.epoch++; this.student = id; this.images.clear(); this.persist('selected', { actor: this.currentIdentity.actor_id, student: id }); this.changed() }
  private check(ctx: Context) { if (this.epoch !== ctx.epoch || !this.session || this.student !== ctx.student) throw new StudentError('session_changed', 401) }
  private async read<T>(ctx: Context, path: string, shape: string) { this.check(ctx); const value = await this.api.request<T>(path, shape); this.check(ctx); return value }
  private async verifyIdentity(): Promise<IdentityContext> {
    const epoch = this.epoch
    let identity: IdentityContext
    try { identity = await this.api.request<IdentityContext>('/v1/me', 'IdentityContext') }
    catch (error) { if (epoch === this.epoch) { this.epoch++; this.currentIdentity = null; this.student = null; this.images.clear(); this.changed() } throw error }
    if (epoch !== this.epoch) throw new StudentError('session_changed', 401)
    if (this.currentIdentity && this.currentIdentity.actor_id !== identity.actor_id) { this.disconnect(); throw new StudentError('session_changed', 401) }
    if (this.student && !identity.authorized_students.some(s => s.student_id === this.student)) {
      this.epoch++; this.currentIdentity = null; this.student = null; this.images.clear(); this.changed()
      throw new StudentError('access_denied', 403)
    }
    const changed = JSON.stringify(identity) !== JSON.stringify(this.currentIdentity)
    this.currentIdentity = identity
    // Initial pairing/restoration publishes once selection is ready. Emitting
    // earlier lets subscribers race authorize() against student selection.
    if (changed && this.student) this.changed(); return identity
  }
  async restore(): Promise<IdentityContext | null> {
    const pending = this.load<PendingPair>('pair'); if (pending?.code && pending.key) return this.pair(pending.code)
    const session = this.load<Session>('session')
    if (!session || typeof session.access_token !== 'string' || Date.parse(session.expires_at) <= Date.now()) { this.disconnect(); return null }
    this.session = session
    try {
      const identity = await this.verifyIdentity(); const selected = this.load<{ actor: string; student: string }>('selected')
      const student = selected?.actor === identity.actor_id && identity.authorized_students.some(s => s.student_id === selected.student) ? selected.student : identity.authorized_students[0]?.student_id
      if (student) this.selectStudent(student); return identity
    } catch (e) { this.currentIdentity = null; this.student = null; this.changed(); throw e }
  }
  async pair(code: string): Promise<IdentityContext> {
    const trimmed = code.trim(); if (!trimmed || trimmed.length > 256) throw new StudentError('invalid_pair_code', 422)
    let pending = this.load<PendingPair>('pair')
    if (pending && pending.code !== trimmed) throw new StudentError('pair_unknown', 409, '上一配对尚未确认，请先重试原配对码。')
    pending ??= { code: trimmed, key: crypto.randomUUID() }
    this.persist('pair', pending) // Synchronous before the first await: UI may now remove its URL fragment.
    this.epoch++; this.currentIdentity = null; this.student = null; this.images.clear(); this.changed()
    const epoch = this.epoch
    try {
      const session = await this.api.request<Session>('/v1/device-sessions', 'PairSession', JSON.stringify({ pairing_code: pending.code }), pending.key, true)
      if (epoch !== this.epoch) throw new StudentError('session_changed', 401)
      this.session = session
      this.persist('session', this.session)
      const identity = await this.verifyIdentity(); this.storage.removeItem(this.storageKey('pair'))
      if (identity.authorized_students[0]) this.selectStudent(identity.authorized_students[0].student_id)
      return identity
    } catch (e) { if (e instanceof StudentError && [401, 403, 422].includes(e.status)) this.storage.removeItem(this.storageKey('pair')); throw e }
  }
  private async authorize(): Promise<Context> {
    if (!this.session) throw new StudentError('authentication_required', 401)
    if (!this.student) throw new StudentError('access_denied', 403)
    const wanted = this.student, identity = await this.verifyIdentity()
    if (!wanted || this.student !== wanted || !identity.authorized_students.some(s => s.student_id === wanted)) { this.student = null; this.images.clear(); this.changed(); throw new StudentError('access_denied', 403) }
    return { student: wanted, epoch: this.epoch, scope: JSON.stringify([this.origin, identity.actor_id, wanted]) }
  }
  private async write<T>(ctx: Context, id: string, path: string, payload: unknown, shape: string, requestShape: string): Promise<T> {
    this.check(ctx)
    if (!valid(requestShape, payload, true)) throw new StudentError('invalid_request', 422)
    const op = await this.store.reserve({ scope: ctx.scope, id, path, key: crypto.randomUUID(), body: JSON.stringify(payload), shape })
    this.check(ctx)
    if (op.path !== path || op.shape !== shape) throw new StudentError('journal_conflict', 409)
    return this.replay<T>(ctx, op)
  }
  private async replay<T>(ctx: Context, op: JournalEntry): Promise<T> {
    this.check(ctx)
    if (op.response !== undefined) return op.response as T
    try {
      // The durable original body wins over a newly-read revision after an unknown write.
      const value = await this.api.request<T>(op.path, op.shape, op.body, op.key)
      this.check(ctx); await this.store.saveOperation({ ...op, response: value }); return value
    } catch (e) {
      if (e instanceof StudentError && e.status === 409 && ['revision_conflict', 'stale_input'].includes(e.code)) await this.store.removeOperation(ctx.scope, op.id)
      // Authorization/visibility failure cannot disprove an earlier unknown
      // commit. Keep its key/body, as with unknown/idempotency-conflict responses.
      throw e
    }
  }
  private view(d: StoredDraft): DraftView { return { id: d.id, title: d.title, stage: d.stage, createdAt: d.createdAt, updatedAt: d.updatedAt, homeworkId: d.homeworkId, error: d.error, photo: d.photo?.blob ?? null, detail: d.detail, correction: d.correction ? { caseId: d.correction.caseId, parentAttemptId: d.correction.parentAttemptId } : undefined } }
  async listDrafts() { const ctx = await this.authorize(); const drafts = await this.store.drafts(ctx.scope); this.check(ctx); return drafts.map(d => { const { photo: _p, detail: _d, ...summary } = this.view(d); return summary }) }
  async getDraft(id: string) { const ctx = await this.authorize(); const d = await this.store.draft(ctx.scope, id); this.check(ctx); return this.view(d) }
  private async save(ctx: Context, d: StoredDraft) { this.check(ctx); d.updatedAt = new Date().toISOString(); await this.store.saveDraft(d); this.check(ctx); this.changed() }
  async createDraft(file: Blob, title = '学校作业') { const ctx = await this.authorize(); const photo = await this.normalize(file); this.check(ctx); const now = new Date().toISOString(); const d: StoredDraft = { id: crypto.randomUUID(), title: title.trim().slice(0, 120) || '学校作业', stage: 'local', createdAt: now, updatedAt: now, homeworkId: null, error: null, scope: ctx.scope, photo, detail: null, generation: 0, ticketGeneration: 0, checkJobs: {} }; await this.save(ctx, d); return this.view(d) }
  private async caseDetail(ctx: Context, id: string): Promise<CaseDetail> {
    const c = await this.read<CorrectionCase>(ctx, `/v1/cases/${encoded(id)}`, 'CorrectionCase')
    if (c.scope.student_id !== ctx.student || c.lifecycle !== 'active') throw new StudentError('access_denied', 403)
    const q = await this.read<QuestionRead>(ctx, `/v1/questions/${encoded(c.question_id)}`, 'QuestionRead')
    const a = await this.read<AttemptRead>(ctx, `/v1/attempts/${encoded(c.current_attempt_id)}`, 'AttemptRead')
    if (!q.currently_effective || !a.currently_effective || q.record.revision !== c.current_question_revision || a.record.case_id !== c.id || a.record.question_id !== c.question_id) throw new StudentError('stale_detail', 409)
    return { case: c, question: q.record, attempt: a.record }
  }
  private async detail(ctx: Context, id: string): Promise<HomeworkDetail> {
    const homework = await this.read<HomeworkSession>(ctx, `/v1/homeworks/${encoded(id)}`, 'HomeworkSession')
    if (homework.scope.student_id !== ctx.student) throw new StudentError('access_denied', 403)
    const cases: CaseDetail[] = [], jobs: Job[] = []
    for (const id of homework.case_ids) cases.push(await this.caseDetail(ctx, id))
    for (const id of homework.job_ids) jobs.push(await this.read<Job>(ctx, `/v1/jobs/${encoded(id)}`, 'Job'))
    return { homework, cases, jobs }
  }
  async history(cursor?: string): Promise<HomeworkPage> { const ctx = await this.authorize(); const params = new URLSearchParams({ limit: '30' }); if (cursor) params.set('cursor', cursor); return this.read(ctx, `/v1/students/${encoded(ctx.student)}/homeworks?${params}`, 'HomeworkPage') }
  async openHomework(id: string): Promise<HomeworkDetail> {
    const ctx = await this.authorize(), detail = await this.detail(ctx, id), drafts = await this.store.drafts(ctx.scope)
    let d = drafts.find(d => d.homeworkId === id && !d.correction)
    if (!d) { const now = new Date().toISOString(); d = { id: remoteDraftId(id), scope: ctx.scope, title: detail.homework.title ?? '学校作业', stage: 'waiting', createdAt: detail.homework.created_at, updatedAt: now, homeworkId: id, error: null, photo: null, detail, generation: 0, ticketGeneration: 0, checkJobs: {} } }
    const recognition = [...detail.jobs].reverse().find(j => j.kind === 'recognize_homework')
    // Do not bypass an uncertain local write by adopting a remote job. The
    // original journal must replay its own key/body before advancing.
    d.detail = detail
    if (!d.photo) d.recognitionJobId ??= recognition?.id
    if (recognition?.status === 'failed') d.stage = 'failed'
    else if (detail.jobs.some(active)) d.stage = 'waiting'
    else d.stage = detail.cases.every(row => effectiveDecision(row) || row.attempt.recognition_status !== 'ready') ? 'complete' : 'waiting'
    detail.draftId = d.id
    await this.save(ctx, d)
    return detail
  }
  async assetBlob(id: string): Promise<Blob> {
    const ctx = await this.authorize(), key = ctx.scope + ':' + id
    const existing = this.images.get(key); if (existing) return existing
    const work = (async () => {
      const asset = await this.read<ImageAsset>(ctx, `/v1/assets/${encoded(id)}`, 'ImageAsset')
      const homework = await this.read<HomeworkSession>(ctx, `/v1/homeworks/${encoded(asset.homework_id)}`, 'HomeworkSession')
      if (homework.scope.student_id !== ctx.student) throw new StudentError('access_denied', 403)
      for (let attempt = 0; attempt < 2; attempt++) {
        const access = await this.read<AssetAccess>(ctx, `/v1/assets/${encoded(id)}/access`, 'AssetAccess')
        try { const blob = await this.api.image(access.read_url); this.check(ctx); return blob } catch (e) { if (!(e instanceof StudentError) || e.status !== 401 || attempt) throw e }
      }
      throw new StudentError('image_unavailable')
    })()
    this.images.set(key, work)
    try { return await work } catch (e) { this.images.delete(key); throw e }
  }
  async createCorrection(caseId: string, file: Blob, reportedHelp: CreateCorrection['reported_help'] = 'unknown', expectedParentAttemptId?: string): Promise<DraftView> {
    const ctx = await this.authorize(), row = await this.caseDetail(ctx, caseId)
    if (expectedParentAttemptId && row.attempt.id !== expectedParentAttemptId) throw new StudentError('newer_correction_exists', 409)
    const photo = await this.normalize(file); this.check(ctx)
    const now = new Date().toISOString(), d: StoredDraft = { id: crypto.randomUUID(), scope: ctx.scope, title: '新订正照片', stage: 'local', createdAt: now, updatedAt: now, homeworkId: row.case.homework_id, photo, detail: null, error: null, generation: 0, ticketGeneration: 0, checkJobs: {}, correction: { caseId, parentAttemptId: row.case.current_attempt_id, reportedHelp } }
    await this.save(ctx, d); return this.view(d)
  }
  private async awaitJob(ctx: Context, id: string): Promise<Job> {
    for (let count = 0; count < 120; count++) {
      const job = await this.read<Job>(ctx, `/v1/jobs/${encoded(id)}`, 'Job')
      if (!active(job)) return job
      await this.sleep(Math.min(5000, Math.max(1000, (job.retry_after_seconds ?? 2) * 1000))); this.check(ctx)
    }
    throw new StudentError('job_waiting', 0, '任务仍在处理中，可稍后打开这份记录继续查看。')
  }
  private jobSucceeded(job: Job) { if (job.status !== 'succeeded') throw new StudentError(`job_${job.status}`, 0, job.status === 'failed' ? '本次识别或检查未完成。原图和失败记录已保留，可明确重试未完成部分。' : '任务版本已变化，请刷新作业查看最新结果。') }
  runDraft(id: string, options: { retryFailed?: boolean } = {}): Promise<DraftView> {
    const key = `${this.epoch}:${id}`; const existing = this.runs.get(key); if (existing) return existing
    const run = async () => { const ctx = await this.authorize(); if (typeof navigator !== 'undefined' && navigator.locks) return navigator.locks.request(`xueban:${ctx.scope}:${id}`, () => this.process(ctx, id, options)); return this.process(ctx, id, options) }
    const result = run().finally(() => { this.runs.delete(key) }); this.runs.set(key, result); return result
  }
  private async process(ctx: Context, id: string, options: { retryFailed?: boolean }): Promise<DraftView> {
    const d = await this.store.draft(ctx.scope, id); this.check(ctx)
    const journalId = d.journalId ?? id
    const post = <T>(name: string, path: string, body: unknown, shape: string, requestShape: string) => this.write<T>(ctx, `${journalId}:${name}`, path, body, shape, requestShape)
    try {
      // Reconcile every original unknown write, even if a remote Worker already
      // produced cases/decisions. Do not skip its journal just because data exists.
      for (const pending of await this.store.pending(ctx.scope, `${journalId}:`)) await this.replay(ctx, pending)
      const recognitionOp = await this.store.operation(ctx.scope, `${journalId}:${d.correction ? 'correction' : 'recognition'}-${d.generation}`)
      if (!d.recognitionJobId && recognitionOp?.response) d.recognitionJobId = (recognitionOp.response as Accepted).job.id
      if (options.retryFailed && d.stage === 'failed' && !await this.store.hasPending(ctx.scope, `${journalId}:`)) {
        if (d.recognitionJobId) {
          const job = await this.read<Job>(ctx, `/v1/jobs/${encoded(d.recognitionJobId)}`, 'Job')
          if (['failed', 'superseded', 'cancelled'].includes(job.status)) {
            if (d.correction) {
              const old = await this.store.operation(ctx.scope, `${journalId}:correction-${d.generation}`)
              const created = (old?.response as Accepted | undefined)?.created_resource
              const row = await this.caseDetail(ctx, d.correction.caseId)
              // A known failed recognition may be retried as another immutable
              // attempt, but never overwrite a newer answer from another device.
              if (created?.resource_type !== 'attempt' || created.resource_id !== row.attempt.id) throw new StudentError('newer_correction_exists', 409)
              d.correction.parentAttemptId = row.attempt.id
            }
            d.recognitionJobId = undefined
          }
        }
        d.generation++; d.checkJobs = {}
        await this.save(ctx, d)
      }
      d.error = null
      if (!d.homeworkId) {
        d.stage = 'creating'; await this.save(ctx, d)
        const hw = await post<HomeworkSession>('homework', `/v1/students/${encoded(ctx.student)}/homeworks`, { title: d.title, occurred_at: d.createdAt, collection_scope: 'partial', curriculum: { stage: 'junior_middle', publisher_code: 'PEP', grade: null, subject: null, volume: null, edition: null } }, 'HomeworkSession', 'CreateHomework')
        d.homeworkId = hw.id; await this.save(ctx, d)
      }
      const path = `/v1/homeworks/${encoded(d.homeworkId)}`
      let hw = await this.read<HomeworkSession>(ctx, path, 'HomeworkSession')
      if (hw.scope.student_id !== ctx.student || hw.processing_blocked) throw new StudentError('access_denied', 403)
      if (d.photo) {
        d.stage = 'uploading'; await this.save(ctx, d)
        let ticket: UploadTicket | undefined
        if (!d.assetId) {
          ticket = await post<UploadTicket>('upload', `${path}/uploads`, { expected_revision: hw.revision, page_index: hw.asset_ids.length, sha256: d.photo.sha256, byte_size: d.photo.size, mime_type: d.photo.mime, width: d.photo.width, height: d.photo.height }, 'UploadTicket', 'CreateUpload')
          d.assetId = ticket.asset.id; await this.save(ctx, d)
        }
        const assetPath = `/v1/assets/${encoded(d.assetId)}`; let asset = await this.read<ImageAsset>(ctx, assetPath, 'ImageAsset')
        if (asset.status !== 'uploaded') {
          if (asset.status !== 'awaiting_upload') throw new StudentError('image_rejected', 422)
          if (!ticket || Date.parse(ticket.expires_at) <= Date.now() + 15_000) {
            const old = await this.store.operation(ctx.scope, `${journalId}:renew-${d.ticketGeneration}`)
            if (!old || old.response !== undefined) { d.ticketGeneration++; await this.save(ctx, d) }
            ticket = await post<UploadTicket>(`renew-${d.ticketGeneration}`, `${assetPath}/upload-ticket`, { expected_revision: asset.revision }, 'UploadTicket', 'RenewUpload')
          }
          this.check(ctx); await this.api.upload(ticket.put_url, ticket.required_headers as Record<string, string>, d.photo.blob); this.check(ctx)
          asset = await this.read<ImageAsset>(ctx, assetPath, 'ImageAsset')
          if (asset.status !== 'uploaded') asset = await post<ImageAsset>('complete', `${assetPath}/complete`, { expected_revision: asset.revision, sha256: d.photo.sha256, byte_size: d.photo.size }, 'ImageAsset', 'CompleteUpload')
          if (asset.status !== 'uploaded') throw new StudentError('upload_incomplete', 422)
        }
      }
      hw = await this.read<HomeworkSession>(ctx, path, 'HomeworkSession')
      if (!d.recognitionJobId && (d.correction || !hw.case_ids.length)) {
        if (!d.photo && d.generation === 0) {
          for (const jobId of [...hw.job_ids].reverse()) { const job = await this.read<Job>(ctx, `/v1/jobs/${encoded(jobId)}`, 'Job'); if (job.kind === 'recognize_homework') { d.recognitionJobId = job.id; break } }
        }
        if (!d.recognitionJobId) {
          d.stage = 'recognizing'; await this.save(ctx, d)
          let accepted: Accepted
          if (d.correction) {
            const c = (await this.caseDetail(ctx, d.correction.caseId)).case
            const op = await this.store.operation(ctx.scope, `${journalId}:correction-${d.generation}`)
            if (!op && c.current_attempt_id !== d.correction.parentAttemptId) throw new StudentError('newer_correction_exists', 409)
            accepted = await post<Accepted>(`correction-${d.generation}`, `/v1/cases/${encoded(c.id)}/attempts`, { expected_revision: c.revision, parent_attempt_id: d.correction.parentAttemptId, question_revision: c.current_question_revision, asset_ids: [d.assetId!], occurred_at: d.createdAt, reported_help: d.correction.reportedHelp }, 'Accepted', 'CreateCorrection')
          } else accepted = await post<Accepted>(`recognition-${d.generation}`, `${path}/recognitions`, { expected_revision: hw.revision, asset_ids: d.assetId ? [d.assetId] : hw.asset_ids }, 'Accepted', 'StartRecognition')
          d.recognitionJobId = accepted.job.id
        }
        await this.save(ctx, d)
      }
      if (d.recognitionJobId) { d.stage = 'recognizing'; await this.save(ctx, d); this.jobSucceeded(await this.awaitJob(ctx, d.recognitionJobId)) }
      d.detail = await this.detail(ctx, d.homeworkId); d.stage = 'checking'; await this.save(ctx, d)
      const ids = d.correction ? [d.correction.caseId] : d.detail.homework.case_ids
      const failures: Job[] = []
      for (const caseId of ids) {
        const row = await this.caseDetail(ctx, caseId)
        if (effectiveDecision(row) || row.attempt.recognition_status !== 'ready') continue
        let job: Job | undefined
        for (const checkId of [...row.case.check_ids].reverse()) {
          const check = await this.read<CheckRun>(ctx, `/v1/checks/${encoded(checkId)}`, 'CheckRun')
          if (matches(check.input_versions, row)) { const candidate = await this.read<Job>(ctx, `/v1/jobs/${encoded(check.job_id)}`, 'Job'); if (active(candidate)) { job = candidate; break } if (candidate.status === 'failed' && !options.retryFailed) { job = candidate; break } }
        }
        if (!job) {
          const version = `${row.attempt.id}-${row.case.current_question_revision}-${row.attempt.recognition_revision}`
          const accepted = await post<Accepted>(`check-${caseId}-${version}-${d.generation}`, `/v1/cases/${encoded(caseId)}/checks`, { expected_revision: row.case.revision, attempt_id: row.attempt.id, question_revision: row.case.current_question_revision, attempt_recognition_revision: row.attempt.recognition_revision }, 'Accepted', 'StartCheck')
          job = accepted.job
        }
        d.checkJobs[caseId] = job.id; await this.save(ctx, d)
        const done = await this.awaitJob(ctx, job.id); if (done.status !== 'succeeded') failures.push(done)
        d.detail = await this.detail(ctx, d.homeworkId); await this.save(ctx, d)
      }
      d.detail = await this.detail(ctx, d.homeworkId)
      if (failures.length) this.jobSucceeded(failures[0])
      d.stage = 'complete'; await this.save(ctx, d); return this.view(d)
    } catch (error) {
      this.check(ctx); d.error = safeError(error); d.stage = error instanceof StudentError && error.code === 'job_waiting' ? 'waiting' : 'failed'
      if (d.homeworkId) { try { d.detail = await this.detail(ctx, d.homeworkId) } catch { this.check(ctx) } }
      await this.save(ctx, d); return this.view(d)
    }
  }
  async requestHint(caseId: string, level: RequestHint['level']): Promise<Hint> {
    const ctx = await this.authorize(), row = await this.caseDetail(ctx, caseId), decision = effectiveDecision(row)
    if (!decision) throw new StudentError('no_current_decision', 409)
    const accepted = await this.write<Accepted>(ctx, `hint:${caseId}:${decision.id}:${decision.revision}:${level}`, `/v1/cases/${encoded(caseId)}/hints`, { expected_revision: row.case.revision, attempt_id: row.attempt.id, source_decision_id: decision.id, source_decision_revision: decision.revision, level }, 'Accepted', 'RequestHint')
    const job = await this.awaitJob(ctx, accepted.job.id)
    if (job.status === 'failed') await this.store.removeOperation(ctx.scope, `hint:${caseId}:${decision.id}:${decision.revision}:${level}`)
    this.jobSucceeded(job)
    if (job.result?.resource_type !== 'hint') throw new StudentError('invalid_response', 502)
    const hint = await this.read<Hint>(ctx, `/v1/hints/${encoded(job.result.resource_id)}`, 'Hint')
    const latest = await this.caseDetail(ctx, caseId)
    if (hint.validity !== 'active' || hint.attempt_id !== latest.attempt.id || hint.source_decision_id !== effectiveDecision(latest)?.id) throw new StudentError('hint_stale', 409)
    return hint
  }
  async recordHintViewed(hintId: string): Promise<Hint> {
    const ctx = await this.authorize(), hint = await this.read<Hint>(ctx, `/v1/hints/${encoded(hintId)}`, 'Hint')
    await this.caseDetail(ctx, hint.case_id)
    return this.write(ctx, `hint-view:${hintId}`, `/v1/hints/${encoded(hintId)}/views`, { expected_revision: hint.revision, shown_at: new Date().toISOString() }, 'Hint', 'ShowHint')
  }
  async requestHelp(caseId: string, kind: CreateHelp['kind'], note: string): Promise<HelpRequest> {
    const ctx = await this.authorize(), row = await this.caseDetail(ctx, caseId)
    const content = note.trim(); if (!content) throw new StudentError('help_note_required', 422, '请简单说明哪里需要老师帮助。')
    return this.write(ctx, `help:${caseId}:${row.attempt.id}:${kind}`, `/v1/cases/${encoded(caseId)}/help-requests`, { expected_revision: row.case.revision, attempt_id: row.attempt.id, kind, note: content }, 'HelpRequest', 'CreateHelp')
  }
}
export const createStudentClient = (options: ClientOptions = {}) => new StudentClient(options)
