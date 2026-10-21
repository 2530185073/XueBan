import type { Account, AdminRequestOptions, AuthSession, Accepted, RecognitionRecoveryStatus, RecognitionRecheckAccepted, AssetAccess, AttemptRead, CaseDetail, CaseRecord, CheckRun, CorrectionCase, CreateCorrection, CreateHelp, DecisionRead, DraftSummary, DraftView, HelpRequest, Hint, HomeworkDetail, HomeworkPage, HomeworkSession, HomeworkSummary, IdentityContext, ImageAsset, InputVersions, Job, JournalEntry, NormalizedPhoto, QuestionRead, RequestHint, Review, StoredDraft, StoredPage, UploadTicket } from './types'
import { StudentApi } from './api'
import { StudentError, safeError } from './errors'
import { DRAFT_FORMAT, DraftStore, LEGACY_PAGE_ID, type PurgeReport } from './store'
import { editPhoto, normalizePhoto } from './photo'
import { applyChange, checkBatch, checkSelection, isIdentity, movePage, pageBudget, removePage, type EditChange, type PageEdit } from './pages'
import { homeworkPageIds } from './homeworkPages'
import { valid } from './validation'
import { bucketOfCase, countBuckets, firstUnresolved, isRetired, orderActiveCases, orderCasesByCreation, orderRowsByPhoto, retiredCases } from './cases'
import { ObjectUrls } from './objectUrls'
import { compareCreated } from './chronology'
import { isCurriculumBooks, isCurriculumCatalog, isKnowledgeDetail, isKnowledgeMap, isStudentCurriculum, isVariantPracticeOverview, isVariantGeneration, isVariantGenerationList, isAgentContext, isMemory, isMemoryList, isOptionalActivities, isPracticeAnswer, isPracticeAssessment, isPracticeHint, isPracticeSession, isPracticeSessionList, MEMORY_CONTENT_MAX, MEMORY_CONTENT_MIN, PRACTICE_ANSWER_MAX, type AgentContext, type CurriculumBooks, type CurriculumCatalog, type ExitReason, type GenerateVariant, type VariantGeneration, type VariantGenerationList, type PracticeSubmission, type KnowledgeDetail, type KnowledgeMap, type Memory, type MemoryList, type OptionalActivities, type PracticeAnswer, type PracticeAssessment, type PracticeHint, type PracticeSession, type StartPractice, type StudentCurriculum, type StudentMemoryCategory, type VariantPracticeOverview } from './learning'

export const remoteDraftId = (id: string) => `remote_${id}`
import type { CurriculumContext } from './types'
export type { CurriculumContext } from './types'
const encoded = encodeURIComponent
const active = (job: Job) => ['queued', 'running', 'retryable_failed'].includes(job.status)
const terminalFailure = (job: Job) => ['failed', 'superseded', 'cancelled'].includes(job.status)
const JOB_POLL_ROUNDS = 120
const latestRecognition = (jobs: Job[]) => jobs.filter(job => job.kind === 'recognize_homework').sort(compareCreated).at(-1)
const matches = (v: InputVersions, row: CaseDetail) => v.question_id === row.case.question_id && v.question_revision === row.case.current_question_revision && v.attempt_id === row.case.current_attempt_id && v.attempt_recognition_revision === row.attempt.recognition_revision
export function effectiveDecision(row: CaseDetail) { const d = row.case.current_decision; return !isRetired(row.case) && d && !['stale', 'retracted'].includes(d.validity) && matches(d.input_versions, row) ? d : null }
/** Requests a student may raise on the current answer; an appeal needs a result to question. */
export function canAppeal(row: CaseDetail) { const d = effectiveDecision(row); return d != null && ['candidate', 'needs_review', 'confirmed'].includes(d.validity) || !isRetired(row.case) && row.case.progress === 'verified_resolved' }
export const openHelp = (help: HelpRequest) => help.status === 'waiting' || help.status === 'claimed'
async function mapLimit<T, R>(items: T[], limit: number, task: (item: T) => Promise<R>): Promise<R[]> {
  const out = new Array<R>(items.length); let next = 0
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, async () => { while (next < items.length) { const index = next++; out[index] = await task(items[index]) } }))
  return out
}
const unique = (ids: (string | null | undefined)[]) => [...new Set(ids.filter((id): id is string => !!id))]
/** Journal name of one page's upload step; each page has its own idempotency keys. */
const pageOp = (page: StoredPage, name: string) => page.id === LEGACY_PAGE_ID ? name : `${name}:${page.id}`
const newPage = (photo: NormalizedPhoto): StoredPage => ({ id: crypto.randomUUID(), photo, rotation: 0, crop: null, uploaded: false, ticketGeneration: 0 })
const bytesOf = (pages: StoredPage[]) => pages.reduce((sum, page) => sum + page.photo.size, 0)
async function readable(blob: Blob, index: number) {
  try { await blob.slice(0, 64).arrayBuffer() } catch { throw new StudentError('photo_unreadable', 0, `第 ${index + 1} 页照片在这台设备上已经读取不到（可能被浏览器清理了）。请重新选择这一页，再继续上传。`) }
}
export function decisionLabel(row: CaseDetail): string {
  const d = effectiveDecision(row)
  if (!d) {
    if (row.attempt.recognition_status !== 'ready') return '识别待核实 · 请重拍或请老师看'
    return '等待检查'
  }
  if (d.validity === 'needs_review' || d.verdict === 'unable_to_judge') return '暂时看不准 · 可以先看其他题'
  const text = { correct: '正确', incorrect: '需要订正', partially_correct: '部分需要订正', unanswered: '尚未作答' }[d.verdict]
  return `${d.validity === 'confirmed' && d.origin === 'staff' ? '老师确认' : 'AI 初判'} · ${text}`
}
interface Context { scope: string; student: string; epoch: number }
interface Session { access_token: string; expires_at: string; kind: 'account' }
interface PendingAuth { body: string; key: string }
export interface ClientOptions { origin?: string; transport?: typeof fetch; storage?: Storage; store?: DraftStore; normalize?: (file: Blob, maxBytes?: number) => Promise<NormalizedPhoto>; edit?: (source: NormalizedPhoto, edit: PageEdit, maxBytes?: number) => Promise<NormalizedPhoto>; sleep?: (ms: number) => Promise<void> }
export class StudentClient {
  private session: Session | null = null
  private currentAccount: Account | null = null
  // Credentials live only during an unresolved request in memory. Never journal them.
  private pendingAuth = new Map<string, PendingAuth>()
  private currentIdentity: IdentityContext | null = null
  private student: string | null = null
  private epoch = 0
  private listeners = new Set<() => void>()
  private api: StudentApi
  private store: DraftStore
  private storage: Storage
  private origin: string
  private normalize: (file: Blob, maxBytes?: number) => Promise<NormalizedPhoto>
  private edit: (source: NormalizedPhoto, edit: PageEdit, maxBytes?: number) => Promise<NormalizedPhoto>
  private sleep: (ms: number) => Promise<void>
  private runs = new Map<string, Promise<DraftView>>()
  private images = new Map<string, Promise<Blob>>()
  private urls = new ObjectUrls()
  private housekeeping: Promise<PurgeReport | null>
  constructor(options: ClientOptions = {}) {
    this.origin = options.origin ?? location.origin
    const url = new URL(this.origin)
    if (url.origin !== this.origin || !['http:', 'https:'].includes(url.protocol)) throw new StudentError('invalid_origin', 422)
    this.storage = options.storage ?? sessionStorage; this.store = options.store ?? new DraftStore()
    this.normalize = options.normalize ?? normalizePhoto; this.edit = options.edit ?? editPhoto; this.sleep = options.sleep ?? (ms => new Promise(resolve => setTimeout(resolve, ms)))
    this.api = new StudentApi(this.origin, () => this.token(), () => this.disconnect(), options.transport)
    this.housekeeping = this.store.purgeExpired().catch(() => null)
  }
  /** blob: URLs for photos of the signed-in student; all are revoked on sign-out or account loss. */
  objectUrl(blob: Blob) { return this.urls.create(blob) }
  releaseObjectUrl(url: string) { this.urls.release(url) }
  private dropMedia() { this.images.clear(); this.urls.releaseAll() }
  get account() { return this.currentAccount }
  get identity() { return this.currentIdentity }
  get selectedStudentId() { return this.student }
  /** Non-secret scope fence for concurrent read-only views, including same-account re-login. */
  get historyScope() { return this.session && this.student && this.currentIdentity ? JSON.stringify([this.epoch, this.currentIdentity.actor_id, this.student]) : null }
  subscribe(listener: () => void) { this.listeners.add(listener); return () => { this.listeners.delete(listener) } }
  private changed() { for (const listener of this.listeners) listener() }
  private storageKey(name: string) { return `xueban:${this.origin}:${name}` }
  private load<T>(name: string): T | null { try { return JSON.parse(this.storage.getItem(this.storageKey(name)) ?? 'null') as T | null } catch { return null } }
  private persist(name: string, value: unknown) { try { this.storage.setItem(this.storageKey(name), JSON.stringify(value)) } catch { throw new StudentError('session_storage_unavailable', 0, '无法保存本次会话，请允许浏览器会话存储后重试。') } }
  private token() { if (this.session && Date.parse(this.session.expires_at) <= Date.now()) this.disconnect(); return this.session?.access_token ?? null }
  private clearSession(clearPending = true) { this.epoch++; this.session = null; this.currentAccount = null; this.currentIdentity = null; this.student = null; this.dropMedia(); if (clearPending) this.pendingAuth.clear(); this.storage.removeItem(this.storageKey('session')); this.storage.removeItem(this.storageKey('selected')); this.storage.removeItem(this.storageKey('pair')); this.storage.removeItem(this.storageKey('scope')); this.changed() }
  disconnect() { this.clearSession() }
  // Identifiers only (already used as IndexedDB scope keys), so a later account
  // failure during restore can still find the device data it must remove.
  private scopeHint(): { actor: string | null; student: string | null } {
    const stored = this.load<{ actor?: unknown; student?: unknown }>('scope')
    return { actor: this.currentIdentity?.actor_id ?? (typeof stored?.actor === 'string' ? stored.actor : null), student: this.student ?? this.currentAccount?.student_id ?? (typeof stored?.student === 'string' ? stored.student : null) }
  }
  private matchesScope(scope: string, hint: { actor: string | null; student: string | null }) {
    try { const [origin, actor, student] = JSON.parse(scope) as unknown[]; return origin === this.origin && student === hint.student && (!hint.actor || actor === hint.actor) } catch { return false }
  }
  /**
   * 403 access_denied, or a server-side 401 while the local token is still
   * valid (session revoked, account disabled or deleted), means this account
   * can no longer use the device data. Remove that scope's drafts, photos and
   * journal; a plain local expiry keeps them for the next sign-in.
   */
  private async identityRequest<T>(path: string, shape: string): Promise<T> {
    const hint = this.scopeHint(), live = !!this.session && Date.parse(this.session.expires_at) > Date.now()
    try { return await this.api.request<T>(path, shape) }
    catch (error) {
      if (!(error instanceof StudentError) || !(error.status === 403 && error.code === 'access_denied' || error.status === 401 && error.code === 'authentication_required' && live)) throw error
      if (hint.student) await this.store.purgeScopes(scope => this.matchesScope(scope, hint)).catch(() => null)
      this.dropMedia(); this.disconnect()
      throw new StudentError('account_unavailable', error.status, error.status === 403 ? '这个账号目前不能查看作业，本机保存的草稿、照片和未确认的提交已清除。请联系老师确认账号。' : '登录已失效或账号已停用，本机保存的草稿和照片已清除。请重新登录。')
    }
  }
  selectStudent(id: string) { if (!this.currentAccount || this.currentAccount.student_id !== id || !this.currentIdentity?.authorized_students.some(s => s.student_id === id)) throw new StudentError('access_denied', 403); if (id !== this.student) { this.epoch++; this.student = id; this.dropMedia(); this.changed() } }
  private check(ctx: Context) { if (this.epoch !== ctx.epoch || !this.session || this.student !== ctx.student) throw new StudentError('session_changed', 401) }
  private async read<T>(ctx: Context, path: string, shape: string) { this.check(ctx); const value = await this.api.request<T>(path, shape); this.check(ctx); return value }
  private async verifyIdentity(): Promise<IdentityContext> {
    const epoch = this.epoch
    let identity: IdentityContext
    try { identity = await this.identityRequest<IdentityContext>('/v1/me', 'IdentityContext') }
    catch (error) { if (epoch === this.epoch) { this.epoch++; this.currentIdentity = null; this.student = null; this.dropMedia(); this.changed() } throw error }
    if (epoch !== this.epoch) throw new StudentError('session_changed', 401)
    if (this.currentIdentity && this.currentIdentity.actor_id !== identity.actor_id) { this.disconnect(); throw new StudentError('session_changed', 401) }
    if (this.student && !identity.authorized_students.some(s => s.student_id === this.student)) {
      this.epoch++; this.currentIdentity = null; this.student = null; this.dropMedia(); this.changed()
      throw new StudentError('access_denied', 403)
    }
    const changed = JSON.stringify(identity) !== JSON.stringify(this.currentIdentity)
    this.currentIdentity = identity
    // Initial login/restoration publishes once selection is ready. Emitting
    // earlier lets subscribers race authorize() against student selection.
    if (changed && this.student) this.changed(); return identity
  }
  private async readyAccount(): Promise<IdentityContext | null> {
    const epoch = this.epoch
    const account = await this.identityRequest<Account>('/v1/auth/account', 'Account')
    if (epoch !== this.epoch) throw new StudentError('session_changed', 401)
    this.currentAccount = account
    if (account.must_change_password) { this.currentIdentity = null; this.student = null; this.changed(); return null }
    const identity = await this.verifyIdentity()
    if (account.student_id) {
      if (!identity.authorized_students.some(s => s.student_id === account.student_id)) { this.disconnect(); throw new StudentError('access_denied', 403) }
      this.student = account.student_id
    } else if (identity.roles.includes('student')) { this.disconnect(); throw new StudentError('access_denied', 403) }
    this.changed(); return identity
  }
  async restore(): Promise<IdentityContext | null> {
    // Pairing is no longer a product login path. Never redeem an old pending code.
    this.storage.removeItem(this.storageKey('pair')); this.storage.removeItem(this.storageKey('selected'))
    const session = this.load<Session>('session')
    if (!session || session.kind !== 'account' || typeof session.access_token !== 'string' || !session.access_token || !Number.isFinite(Date.parse(session.expires_at)) || Date.parse(session.expires_at) <= Date.now()) { this.disconnect(); return null }
    this.session = session
    try { return await this.readyAccount() }
    catch (error) { this.currentAccount = null; this.currentIdentity = null; this.student = null; this.dropMedia(); this.changed(); throw error }
  }
  private async authWrite<T>(path: string, payload: unknown, shape: string, publicRequest = false): Promise<T> {
    const body = JSON.stringify(payload), previous = this.pendingAuth.get(path)
    if (previous && previous.body !== body) throw new StudentError('auth_request_unknown', 409, '上次操作的结果还未确认，请用刚才相同的内容重试；也可以刷新后重新登录。')
    const pending = previous ?? { body, key: crypto.randomUUID() }; this.pendingAuth.set(path, pending)
    try { const result = await this.api.request<T>(path, shape, pending.body, pending.key, publicRequest); this.pendingAuth.delete(path); return result }
    catch (error) { if (error instanceof StudentError && [400, 401, 403, 422, 429].includes(error.status)) this.pendingAuth.delete(path); throw error }
  }
  private acceptSession(value: AuthSession) {
    if (Date.parse(value.expires_at) <= Date.now()) throw new StudentError('authentication_required', 401)
    this.session = { access_token: value.access_token, expires_at: value.expires_at, kind: 'account' }
    this.persist('session', this.session)
  }
  async login(username: string, password: string): Promise<IdentityContext | null> {
    const normalized = username.trim().toLowerCase()
    if (!/^[a-z0-9][a-z0-9._-]{2,63}$/.test(normalized) || !password || [...password].length > 128 || new TextEncoder().encode(password).length > 512) throw new StudentError('invalid_login', 422, '请填写正确的账号和密码。')
    this.clearSession(false); const epoch = this.epoch
    const result = await this.authWrite<AuthSession>('/v1/auth/login', { username: normalized, password }, 'AuthSession', true)
    if (epoch !== this.epoch) throw new StudentError('session_changed', 401)
    this.acceptSession(result)
    return this.readyAccount()
  }
  async changePassword(currentPassword: string, newPassword: string): Promise<IdentityContext | null> {
    if (!this.session) throw new StudentError('authentication_required', 401)
    if (!currentPassword || [...currentPassword].length > 128 || new TextEncoder().encode(currentPassword).length > 512 || [...newPassword].length < 15 || [...newPassword].length > 128 || new TextEncoder().encode(newPassword).length > 512) throw new StudentError('invalid_password', 422, '新密码请使用 15–128 个字符，可以包含空格；当前密码也需要填写。')
    const epoch = this.epoch
    const result = await this.authWrite<AuthSession>('/v1/auth/change-password', { current_password: currentPassword, new_password: newPassword }, 'AuthSession')
    if (epoch !== this.epoch) throw new StudentError('session_changed', 401)
    this.epoch++; this.currentIdentity = null; this.student = null; this.dropMedia(); this.acceptSession(result)
    return this.readyAccount()
  }
  async logout(): Promise<void> {
    if (!this.session) { this.disconnect(); return }
    try { await this.authWrite('/v1/auth/logout', {}, 'LoggedOut') }
    catch (error) { if (!(error instanceof StudentError) || error.status !== 401) throw new StudentError('logout_unconfirmed', 0, '本设备已退出，但服务器尚未确认会话撤销。网络恢复后，原会话仍可能有效直到到期。') }
    finally { this.disconnect() }
  }
  async adminRequest<T>(path: string, options: AdminRequestOptions = {}): Promise<T> {
    if (!/^\/v1\/admin\/[a-z0-9/_?=&.%+-]+$/i.test(path)) throw new StudentError('invalid_path', 422)
    if (!this.currentIdentity?.roles.includes('campus_admin') || this.currentAccount?.must_change_password) throw new StudentError('access_denied', 403)
    const epoch = this.epoch, post = options.method === 'POST'
    if (post && !options.idempotencyKey || !post && options.body !== undefined) throw new StudentError('invalid_request', 422)
    const result = await this.api.request<T>(path, 'AdminResponse', post ? JSON.stringify(options.body ?? {}) : undefined, options.idempotencyKey)
    if (epoch !== this.epoch) throw new StudentError('session_changed', 401)
    return result
  }
  private async authorize(): Promise<Context> {
    if (!this.session) throw new StudentError('authentication_required', 401)
    if (this.currentAccount?.must_change_password) throw new StudentError('password_change_required', 403)
    if (!this.student) throw new StudentError('access_denied', 403)
    const wanted = this.student, identity = await this.verifyIdentity()
    if (!wanted || this.student !== wanted || !identity.authorized_students.some(s => s.student_id === wanted)) { this.student = null; this.dropMedia(); this.changed(); throw new StudentError('access_denied', 403) }
    await this.housekeeping
    const hint = JSON.stringify({ actor: identity.actor_id, student: wanted })
    if (this.storage.getItem(this.storageKey('scope')) !== hint) try { this.storage.setItem(this.storageKey('scope'), hint) } catch { /* Only an optimization for later cleanup. */ }
    const ctx = { student: wanted, epoch: this.epoch, scope: JSON.stringify([this.origin, identity.actor_id, wanted]) }
    this.check(ctx); return ctx
  }
  /** One-time message about unconfirmed submissions that expired on this device. */
  async takeDeviceNotice(): Promise<string | null> {
    const ctx = await this.authorize(), notice = await this.store.takeNotice(ctx.scope); this.check(ctx)
    return notice ? `本机有 ${notice.expiredPending} 条超过 7 天仍未确认结果的提交已清除。服务器上已保存的作业不受影响；如发现缺少照片，请重新拍照提交。` : null
  }
  /** Removes this student's drafts, photos and journal from this device only. */
  async clearLocalData(): Promise<PurgeReport> {
    const ctx = await this.authorize()
    const report = await this.store.purgeScopes(scope => scope === ctx.scope)
    this.check(ctx); this.dropMedia(); this.changed(); return report
  }
  private async write<T>(ctx: Context, id: string, path: string, payload: unknown, shape: string, requestShape: string): Promise<T> {
    this.check(ctx)
    if (!valid(requestShape, payload, true)) throw new StudentError('invalid_request', 422)
    const op = await this.store.reserve({ scope: ctx.scope, id, path, key: crypto.randomUUID(), body: JSON.stringify(payload), shape, updatedAt: new Date().toISOString() })
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
  private view(d: StoredDraft): DraftView {
    const pages = d.pages.map(p => ({ id: p.id, blob: p.photo.blob, width: p.photo.width, height: p.photo.height, size: p.photo.size, edited: !!p.original, assetId: p.assetId, uploaded: p.uploaded }))
    return { id: d.id, title: d.title, stage: d.stage, createdAt: d.createdAt, updatedAt: d.updatedAt, homeworkId: d.homeworkId, error: d.error, pageCount: pages.length, uploadedCount: pages.filter(p => p.uploaded).length, photo: pages[0]?.blob ?? null, pages, editable: !d.locked, detail: d.detail, curriculum: d.curriculum ?? null, correction: d.correction ? { caseId: d.correction.caseId, parentAttemptId: d.correction.parentAttemptId } : undefined }
  }
  async listDrafts(): Promise<DraftSummary[]> { const ctx = await this.authorize(); const drafts = await this.store.drafts(ctx.scope); this.check(ctx); return drafts.map(d => { const { photo: _p, detail: _d, pages: _pages, editable: _e, ...summary } = this.view(d); return summary }) }
  async getDraft(id: string) { const ctx = await this.authorize(); const d = await this.store.draft(ctx.scope, id); this.check(ctx); return this.view(d) }
  private async save(ctx: Context, d: StoredDraft) { this.check(ctx); d.updatedAt = new Date().toISOString(); await this.store.saveDraft(d); this.check(ctx); this.changed() }
  /** Re-encodes a whole selection or none of it, sharing the remaining batch bytes between the new pages. */
  private async importPages(files: Blob[], existing: StoredPage[]): Promise<StoredPage[]> {
    const pages: StoredPage[] = []
    for (const [index, file] of files.entries()) {
      let photo: NormalizedPhoto
      try { photo = await this.normalize(file, pageBudget(bytesOf(existing) + bytesOf(pages), files.length - index)) }
      catch (e) { if (files.length < 2 || !(e instanceof StudentError)) throw e; throw new StudentError(e.code, e.status, `第 ${index + 1} 张：${e.message} 这次选的照片都还没有加入。`) }
      pages.push(newPage(photo))
    }
    checkBatch([...existing, ...pages].map(page => page.photo.size))
    return pages
  }
  async createDraft(files: Blob | Blob[], title = '学校作业', curriculum: CurriculumContext | null = null) {
    const list = Array.isArray(files) ? files : [files]; checkSelection(0, list)
    const ctx = await this.authorize(); const pages = await this.importPages(list, []); this.check(ctx)
    const now = new Date().toISOString(); const d: StoredDraft = { format: DRAFT_FORMAT, id: crypto.randomUUID(), title: title.trim().slice(0, 120) || '学校作业', stage: 'local', createdAt: now, updatedAt: now, homeworkId: null, error: null, scope: ctx.scope, pages, locked: false, detail: null, generation: 0, checkJobs: {}, curriculum }
    await this.save(ctx, d); return this.view(d)
  }
  /** Applies a page change to a draft that has sent nothing yet. */
  private async changePages(id: string, change: (d: StoredDraft) => Promise<StoredPage[]> | StoredPage[]): Promise<DraftView | null> {
    const ctx = await this.authorize(), d = await this.store.draft(ctx.scope, id); this.check(ctx)
    if (d.locked) throw new StudentError('draft_locked', 409, '这份作业已经开始上传，页面不能再调整。可以继续未完成的检查。')
    const pages = await change(d); this.check(ctx)
    if (!pages.length) { await this.store.deleteDraft(ctx.scope, id); this.check(ctx); this.changed(); return null }
    checkBatch(pages.map(page => page.photo.size)); d.pages = pages; await this.save(ctx, d); return this.view(d)
  }
  addPages(id: string, files: Blob[]) {
    return this.changePages(id, async d => {
      if (d.correction) throw new StudentError('correction_single_photo', 422, '订正一次只拍这道题的一张照片。')
      checkSelection(d.pages.length, files); return [...d.pages, ...await this.importPages(files, d.pages)]
    })
  }
  /** Removing the last page discards the draft and returns null. */
  removePage(id: string, pageId: string) { return this.changePages(id, d => removePage(d.pages, pageId)) }
  movePage(id: string, pageId: string, delta: number) { return this.changePages(id, d => movePage(d.pages, pageId, delta)) }
  /** Turns or crops a page, always re-rendered from the imported original. */
  editPage(id: string, pageId: string, change: EditChange) {
    return this.changePages(id, async d => {
      const index = d.pages.findIndex(page => page.id === pageId); if (index < 0) throw new StudentError('page_not_found', 404, '这一页已经不在草稿里了，请刷新后再试。')
      const page = d.pages[index], source = page.original ?? page.photo, next = applyChange(page, change)
      const photo = isIdentity(next) ? source : await this.edit(source, next, pageBudget(bytesOf(d.pages) - page.photo.size))
      const pages = [...d.pages]; pages[index] = { ...page, ...next, photo, original: isIdentity(next) ? undefined : source }; return pages
    })
  }
  /** A local draft that has sent nothing can be thrown away; anything already sent stays recoverable. */
  async discardDraft(id: string) {
    const ctx = await this.authorize(), d = await this.store.draft(ctx.scope, id); this.check(ctx)
    if (d.locked || d.homeworkId || await this.store.hasPending(ctx.scope, `${d.journalId ?? d.id}:`)) throw new StudentError('draft_locked', 409, '这份作业已经开始上传，不能直接删除。可以继续未完成的检查，或在历史记录里清除本机草稿。')
    await this.store.deleteDraft(ctx.scope, id); this.check(ctx); this.changed()
  }
  /**
   * Swaps in a newly chosen photo for a page the server has not received,
   * e.g. after the browser lost the local copy mid-upload. The page gets a new
   * id so its upload uses new keys; an abandoned reservation can at most leave
   * an unused, never-uploaded asset on the server.
   */
  async replacePage(id: string, pageId: string, file: Blob): Promise<DraftView> {
    checkSelection(0, [file])
    const ctx = await this.authorize(), d = await this.store.draft(ctx.scope, id); this.check(ctx)
    const index = d.pages.findIndex(page => page.id === pageId); if (index < 0) throw new StudentError('page_not_found', 404, '这一页已经不在草稿里了，请刷新后再试。')
    const page = d.pages[index], journalId = d.journalId ?? d.id, received = new StudentError('page_uploaded', 409, '这一页服务器已经收到了，不需要重新选择。请继续未完成的检查。')
    if (page.uploaded) throw received
    const assetId = page.assetId ?? ((await this.store.operation(ctx.scope, `${journalId}:${pageOp(page, 'upload')}`))?.response as UploadTicket | undefined)?.asset.id
    if (assetId && (await this.read<ImageAsset>(ctx, `/v1/assets/${encoded(assetId)}`, 'ImageAsset')).status === 'uploaded') { page.assetId = assetId; page.uploaded = true; await this.save(ctx, d); throw received }
    const photo = await this.normalize(file, pageBudget(bytesOf(d.pages) - page.photo.size)); this.check(ctx)
    for (let n = 0; n <= page.ticketGeneration; n++) await this.store.removeOperation(ctx.scope, `${journalId}:${pageOp(page, `renew-${n}`)}`)
    for (const name of ['upload', 'complete']) await this.store.removeOperation(ctx.scope, `${journalId}:${pageOp(page, name)}`)
    d.pages[index] = newPage(photo); if (d.error?.code === 'photo_unreadable' || d.error?.code === 'image_rejected') d.error = null
    await this.save(ctx, d); return this.view(d)
  }
  /** Original homework pages in page order, without later correction photos. */
  async homeworkPages(detail: HomeworkDetail): Promise<string[]> {
    const ctx = await this.authorize(), hw = detail.homework
    if (hw.scope.student_id !== ctx.student) throw new StudentError('access_denied', 403)
    const assets = await mapLimit(hw.asset_ids, 3, id => this.read<ImageAsset>(ctx, `/v1/assets/${encoded(id)}`, 'ImageAsset'))
    return homeworkPageIds(assets.filter(a => a.homework_id === hw.id), detail.jobs, detail.cases)
  }
  private async caseRecordRead(ctx: Context, id: string, homeworkId?: string): Promise<CorrectionCase> {
    const c = await this.read<CorrectionCase>(ctx, `/v1/cases/${encoded(id)}`, 'CorrectionCase')
    if (c.scope.student_id !== ctx.student || homeworkId && c.homework_id !== homeworkId) throw new StudentError('access_denied', 403)
    return c
  }
  private async rowFor(ctx: Context, c: CorrectionCase): Promise<CaseDetail> {
    if (c.lifecycle === 'superseded') throw new StudentError('case_superseded', 409, '老师已重新切分此题，请刷新后在新的题目上继续。')
    if (isRetired(c)) throw new StudentError('case_unavailable', 404, '这道题的记录正在按要求删除，暂时不能查看。')
    const q = await this.read<QuestionRead>(ctx, `/v1/questions/${encoded(c.question_id)}`, 'QuestionRead')
    const a = await this.read<AttemptRead>(ctx, `/v1/attempts/${encoded(c.current_attempt_id)}`, 'AttemptRead')
    if (!q.currently_effective || !a.currently_effective || q.record.revision !== c.current_question_revision || a.record.case_id !== c.id || a.record.question_id !== c.question_id) throw new StudentError('stale_detail', 409)
    return { case: c, question: q.record, attempt: a.record }
  }
  private async caseDetail(ctx: Context, id: string): Promise<CaseDetail> { return this.rowFor(ctx, await this.caseRecordRead(ctx, id)) }
  private async recoveryStatus(ctx: Context, caseId: string, attemptId: string): Promise<RecognitionRecoveryStatus> {
    const value = await this.read<RecognitionRecoveryStatus>(ctx, `/v1/cases/${encoded(caseId)}/recognition-rechecks/status`, 'RecognitionRecoveryStatus')
    if (value.case_id !== caseId || value.attempt_id !== attemptId) throw new StudentError('stale_input', 409)
    if (value.used + value.remaining !== value.limit || value.active_job_id && value.eligible) throw new StudentError('invalid_response', 502)
    return value
  }
  /** Read-only recovery progress, bound to the current student's original attempt. */
  async recognitionRecoveryStatus(caseId: string, attemptId: string) {
    const ctx = await this.authorize(), c = await this.caseRecordRead(ctx, caseId)
    if (c.current_attempt_id !== attemptId) throw new StudentError('stale_input', 409)
    return this.recoveryStatus(ctx, caseId, attemptId)
  }
  /** Explicit user action only. Unknown writes retain their original key/body across reloads. */
  async startRecognitionRecheck(row: CaseDetail): Promise<RecognitionRecoveryStatus> {
    const ctx = await this.authorize(), current = await this.caseDetail(ctx, row.case.id)
    if (current.attempt.id !== row.attempt.id) throw new StudentError('stale_input', 409)
    const status = await this.recoveryStatus(ctx, row.case.id, row.attempt.id)
    if (status.active_job_id) return status
    const id = `recognition-recheck:${row.case.id}:${row.attempt.id}`
    const previous = await this.store.operation(ctx.scope, id); this.check(ctx)
    // An unresolved submission is replayed once only after an explicit click;
    // neither polling nor a 503 chooses a new idempotency key.
    if (previous?.response === undefined && previous) {
      const accepted = await this.replay<RecognitionRecheckAccepted>(ctx, previous)
      this.checkRecoveryAccepted(accepted, row)
      return this.recoveryStatus(ctx, row.case.id, row.attempt.id)
    }
    if (!status.eligible) return status
    if (current.case.revision !== row.case.revision || current.case.current_question_revision !== row.case.current_question_revision || current.attempt.recognition_revision !== row.attempt.recognition_revision) throw new StudentError('revision_conflict', 409)
    if (previous) await this.store.removeOperation(ctx.scope, id)
    try {
      const accepted = await this.write<RecognitionRecheckAccepted>(ctx, id, `/v1/cases/${encoded(row.case.id)}/recognition-rechecks`, {
        expected_revision: current.case.revision, attempt_id: current.attempt.id,
        question_revision: current.case.current_question_revision, attempt_recognition_revision: current.attempt.recognition_revision,
      }, 'RecognitionRecheckAccepted', 'StartRecognitionRecheck')
      this.checkRecoveryAccepted(accepted, row)
      return this.recoveryStatus(ctx, row.case.id, row.attempt.id)
    } catch (error) {
      // A network/503 result may have committed. Recover by GET, never another POST.
      const recovered = await this.recoveryStatus(ctx, row.case.id, row.attempt.id).catch(() => null)
      if (recovered?.active_job_id) return recovered
      throw error
    }
  }
  private checkRecoveryAccepted(value: RecognitionRecheckAccepted, row: CaseDetail) {
    if (value.job.kind !== 'recognition_recheck' || value.recovery.case_id !== row.case.id || value.recovery.attempt_id !== row.attempt.id
      || value.recovery.active_job_id && value.recovery.active_job_id !== value.job.id
      || value.job.result && (value.job.result.resource_type !== 'attempt' || value.job.result.resource_id !== row.attempt.id)
      || value.created_resource && (value.created_resource.resource_type !== 'attempt' || value.created_resource.resource_id !== row.attempt.id)) throw new StudentError('invalid_response', 502)
  }
  /** All case records of a homework, following layout-repair replacements that the homework list may not name yet. */
  private async caseRecords(ctx: Context, homework: HomeworkSession) {
    const records = new Map<string, CorrectionCase>(), queue = [...homework.case_ids]
    for (let index = 0; index < queue.length && index < 300; index++) {
      if (records.has(queue[index])) continue
      const c = await this.caseRecordRead(ctx, queue[index], homework.id); records.set(c.id, c)
      if (c.lifecycle === 'superseded') for (const next of c.replacement_case_ids) if (!queue.includes(next)) queue.push(next)
    }
    return records
  }
  private async detail(ctx: Context, id: string): Promise<HomeworkDetail> {
    const homework = await this.read<HomeworkSession>(ctx, `/v1/homeworks/${encoded(id)}`, 'HomeworkSession')
    if (homework.scope.student_id !== ctx.student) throw new StudentError('access_denied', 403)
    const records = await this.caseRecords(ctx, homework), cases: CaseDetail[] = [], jobs: Job[] = []
    for (const caseId of orderActiveCases(homework.case_ids, records)) cases.push(await this.rowFor(ctx, records.get(caseId)!))
    for (const jobId of homework.job_ids) jobs.push(await this.read<Job>(ctx, `/v1/jobs/${encoded(jobId)}`, 'Job'))
    // A failed or queued check is row-level state. Keep it next to the row so
    // the student view can distinguish it from a missing judgment.
    const withCheckJobs = await mapLimit(cases, 3, async row => {
      const checks = await mapLimit(row.case.check_ids, 3, checkId => this.read<CheckRun>(ctx, `/v1/checks/${encoded(checkId)}`, 'CheckRun'))
      for (const check of checks.sort(compareCreated).reverse()) {
        if (matches(check.input_versions, row)) {
          const job = jobs.find(candidate => candidate.id === check.job_id)
          if (job) return { ...row, checkJob: job }
        }
      }
      return row
    })
    const rows = new Map(withCheckJobs.map(row => [row.case.id, row]))
    const ordered = orderCasesByCreation(homework.case_ids, records, withCheckJobs).map(caseId => rows.get(caseId)!)
    return { homework, cases: orderRowsByPhoto(ordered, homework.asset_ids), jobs, retired: retiredCases(records) }
  }
  /** Read-only counts for the history list; never a whole-homework accuracy. */
  async homeworkSummary(homework: HomeworkSession): Promise<HomeworkSummary> { return this.summaryFor(await this.authorize(), homework) }
  /** Summaries for a history page under one authorization; one failing homework does not stop the rest. */
  async homeworkSummaries(homeworks: HomeworkSession[], each: (id: string, summary: HomeworkSummary | null) => void): Promise<void> {
    const ctx = await this.authorize()
    await mapLimit(homeworks, 2, async homework => {
      let summary: HomeworkSummary | null = null
      try { summary = await this.summaryFor(ctx, homework) } catch (e) { if (e instanceof StudentError && e.code === 'session_changed') throw e }
      this.check(ctx); each(homework.id, summary)
    })
  }
  private async summaryFor(ctx: Context, homework: HomeworkSession): Promise<HomeworkSummary> {
    if (homework.scope.student_id !== ctx.student) throw new StudentError('access_denied', 403)
    const records = await this.caseRecords(ctx, homework), active = orderCasesByCreation(homework.case_ids, records).map(id => records.get(id)!)
    return { homeworkId: homework.id, revision: homework.revision, counts: countBuckets(active.map(bucketOfCase)), firstUnresolvedCaseId: firstUnresolved(active, bucketOfCase)?.id ?? null, retired: records.size - active.length }
  }
  /** Every attempt, check decision, shown hint, help request and review of one case. */
  async caseRecord(caseId: string): Promise<CaseRecord> {
    const ctx = await this.authorize(), c = await this.caseRecordRead(ctx, caseId)
    if (c.lifecycle === 'deletion_pending') throw new StudentError('case_unavailable', 404, '这道题的记录正在按要求删除，暂时不能查看。')
    const get = <T>(kind: string, shape: string) => (id: string) => this.read<T>(ctx, `/v1/${kind}/${encoded(id)}`, shape)
    const [attempts, checks, hints, help, reviews] = await Promise.all([
      mapLimit(c.attempt_ids, 3, get<AttemptRead>('attempts', 'AttemptRead')), mapLimit(c.check_ids, 3, get<CheckRun>('checks', 'CheckRun')),
      mapLimit(c.hint_ids, 3, get<Hint>('hints', 'Hint')), mapLimit(c.help_request_ids, 3, get<HelpRequest>('help-requests', 'HelpRequest')), mapLimit(c.review_ids, 3, get<Review>('reviews', 'Review')),
    ])
    const decisionIds = unique([...checks.flatMap(check => check.decision_ids), ...reviews.flatMap(r => [r.target_decision_id, r.new_decision_id]), c.current_decision?.id])
    const decisions = await mapLimit(decisionIds, 3, get<DecisionRead>('decisions', 'DecisionRead'))
    if ([...attempts.map(a => a.record), ...checks, ...hints, ...help, ...reviews, ...decisions.map(d => d.record)].some(item => item.case_id !== c.id)) throw new StudentError('invalid_response', 502)
    this.check(ctx)
    return { case: c, attempts, checks, decisions, hints, help, reviews }
  }
  async history(cursor?: string): Promise<HomeworkPage> { const ctx = await this.authorize(); const params = new URLSearchParams({ limit: '30' }); if (cursor) params.set('cursor', cursor); return this.read(ctx, `/v1/students/${encoded(ctx.student)}/homeworks?${params}`, 'HomeworkPage') }
  async openHomework(id: string): Promise<HomeworkDetail> {
    const ctx = await this.authorize(), detail = await this.detail(ctx, id), drafts = await this.store.drafts(ctx.scope)
    let d = drafts.find(d => d.homeworkId === id && !d.correction)
    if (!d) { const now = new Date().toISOString(); d = { format: DRAFT_FORMAT, id: remoteDraftId(id), scope: ctx.scope, title: detail.homework.title ?? '学校作业', stage: 'waiting', createdAt: detail.homework.created_at, updatedAt: now, homeworkId: id, error: null, pages: [], locked: true, detail, generation: 0, checkJobs: {} } }
    const recognition = latestRecognition(detail.jobs)
    // Do not bypass an uncertain local write by adopting a remote job. The
    // original journal must replay its own key/body before advancing.
    d.detail = detail
    const hasPendingWrite = await this.store.hasPending(ctx.scope, `${d.journalId ?? d.id}:`)
    if (recognition && d.pages.every(page => page.uploaded) && !hasPendingWrite) d.recognitionJobId = recognition.id
    // Pages not yet received, or no recognition started yet, is unfinished work and never shown as complete.
    if (d.pages.some(page => !page.uploaded) || !recognition && !detail.cases.length) d.stage = d.stage === 'failed' ? 'failed' : 'waiting'
    else if (recognition?.status === 'failed') d.stage = 'failed'
    else if (detail.jobs.some(active)) d.stage = 'waiting'
    else if (detail.cases.every(row => effectiveDecision(row) || row.attempt.recognition_status === 'needs_revision')) d.stage = 'complete'
    else if (detail.jobs.some(terminalFailure)) d.stage = 'failed'
    else d.stage = 'waiting'
    if (d.stage === 'complete' && !hasPendingWrite) d.error = null
    detail.draftId = d.id
    await this.save(ctx, d)
    return detail
  }
  /** Refresh the currently opened cache, including a correction-photo draft.
   * GET only: never resumes uploads/checks or creates a replacement draft.
   */
  async refreshRecognitionDraft(id: string, homeworkId: string): Promise<DraftView> {
    const ctx = await this.authorize(), opened = await this.store.draft(ctx.scope, id)
    this.check(ctx)
    if (opened.homeworkId !== homeworkId) throw new StudentError('stale_input', 409)
    const detail = await this.detail(ctx, homeworkId)
    // Re-read after network work so unrelated local edits/operation state survive.
    const latest = await this.store.draft(ctx.scope, id); this.check(ctx)
    if (latest.homeworkId !== homeworkId) throw new StudentError('stale_input', 409)
    latest.detail = { ...detail, draftId: latest.id }
    await this.save(ctx, latest)
    return this.view(latest)
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
    checkSelection(0, [file])
    const photo = await this.normalize(file); this.check(ctx)
    const now = new Date().toISOString(), d: StoredDraft = { format: DRAFT_FORMAT, id: crypto.randomUUID(), scope: ctx.scope, title: '新订正照片', stage: 'local', createdAt: now, updatedAt: now, homeworkId: row.case.homework_id, pages: [newPage(photo)], locked: false, detail: null, error: null, generation: 0, checkJobs: {}, correction: { caseId, parentAttemptId: row.case.current_attempt_id, reportedHelp } }
    await this.save(ctx, d); return this.view(d)
  }
  /** Poll a group as one unit so a slow job cannot prevent the other jobs from being observed. */
  private async pollJobs(ctx: Context, ids: string[]): Promise<Job[]> {
    const latest = new Map<string, Job>(), uniqueIds = unique(ids)
    for (let count = 0; count < JOB_POLL_ROUNDS; count++) {
      const pending = uniqueIds.filter(id => !latest.has(id) || active(latest.get(id)!))
      if (!pending.length) return [...latest.values()]
      const current = await Promise.all(pending.map(id => this.read<Job>(ctx, `/v1/jobs/${encoded(id)}`, 'Job')))
      current.forEach(job => latest.set(job.id, job))
      if (![...latest.values()].some(active)) return [...latest.values()]
      const delay = Math.min(5000, Math.max(1000, Math.max(...current.map(job => job.retry_after_seconds ?? 2)) * 1000))
      await this.sleep(delay); this.check(ctx)
    }
    return [...latest.values()]
  }
  private async awaitJob(ctx: Context, id: string): Promise<Job> {
    const [job] = await this.pollJobs(ctx, [id])
    if (job && !active(job)) return job
    throw new StudentError('job_waiting', 0, '任务仍在处理中，可稍后打开这份记录继续查看。')
  }
  private jobSucceeded(job: Job) { if (job.status !== 'succeeded') throw new StudentError(`job_${job.status}`, 0, job.status === 'failed' ? '本次识别或检查未完成。原图和失败记录已保留，可明确重试未完成部分。' : '任务版本已变化，请刷新作业查看最新结果。') }
  runDraft(id: string, options: { retryFailed?: boolean; rerecognize?: boolean; forceCheckCaseIds?: string[]; forceCheckToken?: string } = {}): Promise<DraftView> {
    const key = `${this.epoch}:${id}`; const existing = this.runs.get(key)
    // Explicit input changes must not inherit an older background run.
    if (existing) return options.forceCheckCaseIds?.length || options.retryFailed || options.rerecognize ? existing.then(() => this.runDraft(id, options)) : existing
    const run = async () => { const ctx = await this.authorize(); if (typeof navigator !== 'undefined' && navigator.locks) return navigator.locks.request(`xueban:${ctx.scope}:${id}`, () => this.process(ctx, id, options)); return this.process(ctx, id, options) }
    const result = run().finally(() => { this.runs.delete(key) }); this.runs.set(key, result); return result
  }
  private async process(ctx: Context, id: string, options: { retryFailed?: boolean; rerecognize?: boolean; forceCheckCaseIds?: string[]; forceCheckToken?: string }): Promise<DraftView> {
    const d = await this.store.draft(ctx.scope, id); this.check(ctx)
    const journalId = d.journalId ?? id
    const post = <T>(name: string, path: string, body: unknown, shape: string, requestShape: string) => this.write<T>(ctx, `${journalId}:${name}`, path, body, shape, requestShape)
    try {
      // Reconcile every original unknown write, even if a remote Worker already
      // produced cases/decisions. Do not skip its journal just because data exists.
      for (const pending of await this.store.pending(ctx.scope, `${journalId}:`)) await this.replay(ctx, pending)
      const recognitionOp = await this.store.operation(ctx.scope, `${journalId}:${d.correction ? 'correction' : 'recognition'}-${d.generation}`)
      if (!d.recognitionJobId && recognitionOp?.response) d.recognitionJobId = (recognitionOp.response as Accepted).job.id
      // A recognition that finished without locating any question is run again on request, as a new generation.
      if (options.rerecognize && !d.correction && d.homeworkId && d.recognitionJobId && !await this.store.hasPending(ctx.scope, `${journalId}:`)) {
        const hw = await this.read<HomeworkSession>(ctx, `/v1/homeworks/${encoded(d.homeworkId)}`, 'HomeworkSession')
        const job = await this.read<Job>(ctx, `/v1/jobs/${encoded(d.recognitionJobId)}`, 'Job')
        if (!hw.case_ids.length && job.status === 'succeeded') { d.recognitionJobId = undefined; d.generation++; d.checkJobs = {}; await this.save(ctx, d) }
      }
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
        const course = d.curriculum
        const hw = await post<HomeworkSession>('homework', `/v1/students/${encoded(ctx.student)}/homeworks`, { title: d.title, occurred_at: d.createdAt, collection_scope: 'partial', curriculum: course ?? { stage: null, publisher_code: null, grade: null, subject: null, volume: null, edition: null } }, 'HomeworkSession', 'CreateHomework')
        d.homeworkId = hw.id; await this.save(ctx, d)
      }
      const path = `/v1/homeworks/${encoded(d.homeworkId)}`
      let hw = await this.read<HomeworkSession>(ctx, path, 'HomeworkSession')
      if (hw.scope.student_id !== ctx.student || hw.processing_blocked) throw new StudentError('access_denied', 403)
      if (d.pages.length) {
        if (!d.locked) checkBatch(d.pages.map(page => page.photo.size))
        d.stage = 'uploading'; d.locked = true; await this.save(ctx, d)
        // Pages go one by one in the saved order; each page's own journal makes a retry resume where it stopped.
        for (const [index, page] of d.pages.entries()) {
          if (page.uploaded) continue
          const op = (name: string) => pageOp(page, name)
          let ticket: UploadTicket | undefined
          if (!page.assetId) {
            await readable(page.photo.blob, index)
            const current = await this.read<HomeworkSession>(ctx, path, 'HomeworkSession')
            ticket = await post<UploadTicket>(op('upload'), `${path}/uploads`, { expected_revision: current.revision, page_index: d.correction ? current.asset_ids.length : index, sha256: page.photo.sha256, byte_size: page.photo.size, mime_type: page.photo.mime, width: page.photo.width, height: page.photo.height }, 'UploadTicket', 'CreateUpload')
            page.assetId = ticket.asset.id; await this.save(ctx, d)
          }
          const assetPath = `/v1/assets/${encoded(page.assetId)}`; let asset = await this.read<ImageAsset>(ctx, assetPath, 'ImageAsset')
          if (asset.status !== 'uploaded') {
            if (asset.status !== 'awaiting_upload') throw new StudentError('image_rejected', 422, `第 ${index + 1} 页照片没有被服务器接收。请重新选择这一页，再继续上传。`)
            await readable(page.photo.blob, index)
            if (!ticket || Date.parse(ticket.expires_at) <= Date.now() + 15_000) {
              const old = await this.store.operation(ctx.scope, `${journalId}:${op(`renew-${page.ticketGeneration}`)}`)
              if (!old || old.response !== undefined) { page.ticketGeneration++; await this.save(ctx, d) }
              ticket = await post<UploadTicket>(op(`renew-${page.ticketGeneration}`), `${assetPath}/upload-ticket`, { expected_revision: asset.revision }, 'UploadTicket', 'RenewUpload')
            }
            this.check(ctx); await this.api.upload(ticket.put_url, ticket.required_headers as Record<string, string>, page.photo.blob); this.check(ctx)
            asset = await this.read<ImageAsset>(ctx, assetPath, 'ImageAsset')
            if (asset.status !== 'uploaded') asset = await post<ImageAsset>(op('complete'), `${assetPath}/complete`, { expected_revision: asset.revision, sha256: page.photo.sha256, byte_size: page.photo.size }, 'ImageAsset', 'CompleteUpload')
            if (asset.status !== 'uploaded') throw new StudentError('upload_incomplete', 422)
          }
          page.uploaded = true; await this.save(ctx, d)
        }
      }
      hw = await this.read<HomeworkSession>(ctx, path, 'HomeworkSession')
      if (!d.recognitionJobId && (d.correction || !hw.case_ids.length)) {
        if (!d.pages.length && d.generation === 0) {
          const jobs = await mapLimit(hw.job_ids, 3, jobId => this.read<Job>(ctx, `/v1/jobs/${encoded(jobId)}`, 'Job'))
          d.recognitionJobId = latestRecognition(jobs)?.id
        }
        if (!d.recognitionJobId) {
          d.stage = 'recognizing'; await this.save(ctx, d)
          let accepted: Accepted
          if (d.correction) {
            const c = (await this.caseDetail(ctx, d.correction.caseId)).case
            const op = await this.store.operation(ctx.scope, `${journalId}:correction-${d.generation}`)
            if (!op && c.current_attempt_id !== d.correction.parentAttemptId) throw new StudentError('newer_correction_exists', 409)
            accepted = await post<Accepted>(`correction-${d.generation}`, `/v1/cases/${encoded(c.id)}/attempts`, { expected_revision: c.revision, parent_attempt_id: d.correction.parentAttemptId, question_revision: c.current_question_revision, asset_ids: [d.pages[0].assetId!], occurred_at: d.createdAt, reported_help: d.correction.reportedHelp }, 'Accepted', 'CreateCorrection')
          } else accepted = await post<Accepted>(`recognition-${d.generation}`, `${path}/recognitions`, { expected_revision: hw.revision, asset_ids: d.pages.length ? d.pages.map(page => page.assetId!) : hw.asset_ids }, 'Accepted', 'StartRecognition')
          d.recognitionJobId = accepted.job.id
        }
        await this.save(ctx, d)
      }
      if (d.recognitionJobId) {
        d.stage = 'recognizing'; await this.save(ctx, d)
        const [recognitionJob] = await this.pollJobs(ctx, [d.recognitionJobId])
        if (!recognitionJob || active(recognitionJob)) {
          d.error = null; d.stage = 'waiting'; d.detail = await this.detail(ctx, d.homeworkId); await this.save(ctx, d); return this.view(d)
        }
        this.jobSucceeded(recognitionJob)
      }
      d.detail = await this.detail(ctx, d.homeworkId)
      // Recovery is a server-owned original-image task, not a new /checks request.
      if (d.detail.jobs.some(job => job.kind === 'recognition_recheck' && active(job))) { d.stage = 'waiting'; d.error = null; await this.save(ctx, d); return this.view(d) }
      d.stage = 'checking'; await this.save(ctx, d)
      const forced = new Set(options.forceCheckCaseIds ?? [])
      const ids = forced.size ? [...forced] : d.correction ? [d.correction.caseId] : d.detail.cases.map(row => row.case.id)
      const knownJobs = new Map(d.detail.jobs.map(job => [job.id, job]))
      const checkJobs = new Map<string, Job>()
      const skipped = new Set<string>()
      for (const caseId of ids) {
        let row: CaseDetail
        try { row = await this.caseDetail(ctx, caseId) } catch (e) { if (!d.correction && e instanceof StudentError && ['case_superseded', 'case_unavailable'].includes(e.code)) continue; throw e }
        if (effectiveDecision(row) && !forced.has(caseId)) continue
        const answerTextPresent = typeof row.attempt.answer_text === 'string' && row.attempt.answer_text.trim().length > 0
        if (row.attempt.recognition_status === 'pending' || row.attempt.recognition_status === 'failed'
          || row.attempt.recognition_status === 'needs_revision' && (!forced.has(caseId) || !answerTextPresent)) {
          skipped.add(caseId); continue
        }
        let job: Job | undefined
        const checks = await mapLimit(row.case.check_ids, 3, checkId => this.read<CheckRun>(ctx, `/v1/checks/${encoded(checkId)}`, 'CheckRun'))
        for (const check of checks.sort(compareCreated).reverse()) {
          if (matches(check.input_versions, row)) {
            const candidate = knownJobs.get(check.job_id) ?? await this.read<Job>(ctx, `/v1/jobs/${encoded(check.job_id)}`, 'Job')
            if (active(candidate)) { job = candidate; break }
            if (candidate.status === 'failed' && !options.retryFailed) { job = candidate; break }
          }
        }
        if (!job) {
          const version = `${row.attempt.id}-${row.case.current_question_revision}-${row.attempt.recognition_revision}`
          const accepted = await post<Accepted>(`check-${caseId}-${version}-${d.generation}${forced.has(caseId) ? `-${options.forceCheckToken ?? crypto.randomUUID()}` : ''}`, `/v1/cases/${encoded(caseId)}/checks`, { expected_revision: row.case.revision, attempt_id: row.attempt.id, question_revision: row.case.current_question_revision, attempt_recognition_revision: row.attempt.recognition_revision }, 'Accepted', 'StartCheck')
          job = accepted.job
        }
        d.checkJobs[caseId] = job.id; checkJobs.set(caseId, job); await this.save(ctx, d)
      }
      const polled = await this.pollJobs(ctx, [...checkJobs.values()].map(job => job.id))
      d.detail = await this.detail(ctx, d.homeworkId)
      if (polled.some(active)) { d.error = null; d.stage = 'waiting'; await this.save(ctx, d); return this.view(d) }
      const failure = polled.find(job => job.status !== 'succeeded')
      if (failure) this.jobSucceeded(failure)
      const unresolved = d.detail.cases.filter(row => (!d.correction || row.case.id === d.correction.caseId) && !effectiveDecision(row))
      if (unresolved.length) {
        if (unresolved.some(row => row.attempt.recognition_status === 'failed')) throw new StudentError('recognition_failed', 0, '部分题目的识别服务未完成，未生成判断；请重试或请坐班老师核实。')
        // A correction photo can finish recognition without enough answer
        // evidence for a second-pass judgment. Keep the case visible for
        // review, but do not turn a completed recognition into a false error.
        if (unresolved.every(row => row.attempt.recognition_status === 'needs_revision')) { d.stage = 'complete'; await this.save(ctx, d); return this.view(d) }
        // A pending attempt or a server-side queued check is unfinished work.
        d.error = null; d.stage = skipped.size ? 'waiting' : 'checking'; await this.save(ctx, d); return this.view(d)
      }
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
  /**
   * One journaled request per answer and kind; an appeal is per result, so a
   * new teacher decision can be appealed again. An unknown earlier write is
   * replayed with its original note before any duplicate check.
   */
  async requestHelp(caseId: string, kind: CreateHelp['kind'], note: string): Promise<HelpRequest> {
    const ctx = await this.authorize(), row = await this.caseDetail(ctx, caseId)
    const content = note.trim(); if (!content) throw new StudentError('help_note_required', 422, '请简单说明哪里需要老师帮助。')
    if ([...content].length > 500) throw new StudentError('help_note_too_long', 422, '说明请控制在 500 字以内。')
    const decision = effectiveDecision(row)
    if (kind === 'appeal' && !canAppeal(row)) throw new StudentError('appeal_unavailable', 409, '这道题还没有可以申请重查的结果，请先等待检查完成。')
    const id = kind === 'appeal' ? `help:${caseId}:${row.attempt.id}:appeal:${decision?.id ?? 'resolved'}:${decision?.revision ?? 0}` : `help:${caseId}:${row.attempt.id}:${kind}`
    let previous = await this.store.operation(ctx.scope, id); this.check(ctx)
    if (previous?.response !== undefined) {
      // A finished earlier request does not block asking again once the teacher closed it.
      const earlier = previous.response as HelpRequest, latest = row.case.help_request_ids.includes(earlier.id) ? await this.read<HelpRequest>(ctx, `/v1/help-requests/${encoded(earlier.id)}`, 'HelpRequest') : null
      if (latest && openHelp(latest)) return latest
      await this.store.removeOperation(ctx.scope, id); previous = undefined
    }
    if (!previous) {
      const existing = await mapLimit(row.case.help_request_ids, 3, helpId => this.read<HelpRequest>(ctx, `/v1/help-requests/${encoded(helpId)}`, 'HelpRequest'))
      if (existing.some(h => h.kind === kind && h.attempt_id === row.attempt.id && openHelp(h))) throw new StudentError('help_already_open', 409, kind === 'appeal' ? '已经申请过老师重查，请等老师处理。' : '这道题已经请过老师，请等老师处理。')
    }
    return this.write(ctx, id, `/v1/cases/${encoded(caseId)}/help-requests`, { expected_revision: row.case.revision, attempt_id: row.attempt.id, kind, note: content }, 'HelpRequest', 'CreateHelp')
  }
  /**
   * Later-phase learning endpoints under the same sign-in, student and
   * account-loss checks as homework reads. Each call sends a fresh
   * Idempotency-Key and these writes are not journaled; callers re-read
   * after an unknown outcome instead of replaying.
   */
  private async learning<T>(path: (student: string) => string, guard: (value: unknown) => value is T, body?: unknown, method?: 'PUT'): Promise<{ value: T; student: string }> {
    const ctx = await this.authorize()
    const value = await this.api.request<unknown>(path(encoded(ctx.student)), 'Learning', body === undefined ? undefined : JSON.stringify(body), body === undefined ? undefined : crypto.randomUUID(), false, method)
    this.check(ctx)
    if (!guard(value)) throw new StudentError('invalid_response', 502)
    return { value, student: ctx.student }
  }
  private owned<T extends { student_id: string }>(result: { value: T; student: string }): T { if (result.value.student_id !== result.student) throw new StudentError('access_denied', 403); return result.value }
  async knowledgeMap(): Promise<KnowledgeMap> { return this.owned(await this.learning(s => `/v1/students/${s}/knowledge`, isKnowledgeMap)) }
  async setQuestionSubject(questionId: string, questionRevision: number, subject: string): Promise<void> {
    const ctx = await this.authorize()
    const value = await this.api.request<unknown>(`/v1/questions/${encoded(questionId)}/subject`, 'Learning',
      JSON.stringify({ expected_question_revision: questionRevision, subject }), crypto.randomUUID(), false, 'PUT')
    this.check(ctx)
    if (typeof value !== 'object' || value === null || (value as { question_id?: unknown }).question_id !== questionId
        || (value as { subject?: unknown }).subject !== subject) throw new StudentError('invalid_response', 502)
  }
  async curriculumCatalog(): Promise<CurriculumCatalog> {
    const ctx = await this.authorize(), value = await this.api.request<unknown>('/v1/curriculum/catalog', 'Learning')
    this.check(ctx); if (!isCurriculumCatalog(value)) throw new StudentError('invalid_response', 502); return value
  }
  async studentCurriculum(): Promise<StudentCurriculum> {
    const { value, student } = await this.learning(s => `/v1/students/${s}/curriculum`, isStudentCurriculum)
    if (value.student_id !== student) throw new StudentError('access_denied', 403)
    return value
  }
  async setStudentGrade(grade: number): Promise<StudentCurriculum> {
    if (![5, 6, 7, 8].includes(grade)) throw new StudentError('invalid_grade', 422, '请选择五至八年级。')
    const profile = await this.learning(s => `/v1/students/${s}/learning-profile`, (value): value is { student_id: string; revision: number } => typeof value === 'object' && value !== null && typeof (value as { revision?: unknown }).revision === 'number' && (value as { student_id?: unknown }).student_id === this.student)
    const current = profile.value as { revision: number }
    const { value, student } = await this.learning(s => `/v1/students/${s}/learning-profile`, isStudentCurriculum, {
      expected_revision: current.revision, grade, term: null, current_knowledge_ids: [], note: null,
    }, 'PUT')
    if (value.student_id !== student) throw new StudentError('access_denied', 403)
    return value
  }
  async curriculumBooks(): Promise<CurriculumBooks> { return this.owned(await this.learning(s => `/v1/students/${s}/curriculum-books`, isCurriculumBooks)) }
  async selectCurriculumBook(graphVersion: string | null, expectedRevision: number): Promise<CurriculumBooks> {
    if (!Number.isInteger(expectedRevision) || expectedRevision < 0 || (graphVersion !== null && (!graphVersion || graphVersion.length > 200))) throw new StudentError('invalid_curriculum_selection', 422)
    return this.owned(await this.learning(s => `/v1/students/${s}/curriculum-books`, isCurriculumBooks, { expected_revision: expectedRevision, graph_version: graphVersion }, 'PUT'))
  }
  async knowledgeNode(knowledgeId: string): Promise<KnowledgeDetail> { return (await this.learning(s => `/v1/students/${s}/knowledge/${encoded(knowledgeId)}`, isKnowledgeDetail)).value }
  async agentContext(caseId: string): Promise<AgentContext> {
    const { value } = await this.learning(() => `/v1/cases/${encoded(caseId)}/agent-context`, isAgentContext)
    if (value.case_id !== caseId) throw new StudentError('invalid_response', 502)
    return value
  }
  async memories(): Promise<MemoryList> { return this.owned(await this.learning(s => `/v1/students/${s}/memories`, isMemoryList)) }
  async addMemory(category: StudentMemoryCategory, content: string): Promise<Memory> {
    const trimmed = content.trim(), length = [...trimmed].length
    if (length < MEMORY_CONTENT_MIN || length > MEMORY_CONTENT_MAX) throw new StudentError('memory_length', 422, `请写 ${MEMORY_CONTENT_MIN}–${MEMORY_CONTENT_MAX} 个字。`)
    return (await this.learning(s => `/v1/students/${s}/memories`, isMemory, { category, content: trimmed })).value
  }
  async retractMemory(memoryId: string): Promise<Memory> { return (await this.learning(() => `/v1/memories/${encoded(memoryId)}/retract`, isMemory, {})).value }
  async optionalActivities(): Promise<OptionalActivities> { return (await this.learning(s => `/v1/students/${s}/optional-activities`, isOptionalActivities)).value }
  async variantPractice(): Promise<VariantPracticeOverview> { return this.owned(await this.learning(s => `/v1/students/${s}/variant-practice`, isVariantPracticeOverview)) }
  async generateVariant(request: GenerateVariant): Promise<VariantGeneration> {
    const value = this.owned(await this.learning(s => `/v1/students/${s}/variant-generations`, isVariantGeneration, request))
    if (value.source_case_id !== request.source_case_id) throw new StudentError('invalid_response', 502)
    return value
  }
  async variantGeneration(jobId: string): Promise<VariantGeneration> {
    const value = this.owned(await this.learning(() => `/v1/variant-generations/${encoded(jobId)}`, isVariantGeneration))
    if (value.job_id !== jobId) throw new StudentError('invalid_response', 502)
    return value
  }
  async variantGenerations(): Promise<VariantGenerationList> {
    const value = this.owned(await this.learning(s => `/v1/students/${s}/variant-generations`, isVariantGenerationList))
    if (value.items.some(item => item.student_id !== value.student_id)) throw new StudentError('access_denied', 403)
    return value
  }
  async startPractice(request: StartPractice): Promise<PracticeSession> { return this.owned(await this.learning(s => `/v1/students/${s}/practice-sessions`, isPracticeSession, request)) }
  async practiceSession(sessionId: string): Promise<PracticeSession> {
    const value = this.owned(await this.learning(() => `/v1/practice-sessions/${encoded(sessionId)}`, isPracticeSession))
    if (value.id !== sessionId) throw new StudentError('invalid_response', 502)
    return value
  }
  async practiceAssessment(assessmentId: string, sessionId: string, itemId: string): Promise<PracticeAssessment> {
    const value = this.owned(await this.learning(() => `/v1/practice-assessments/${encoded(assessmentId)}`, isPracticeAssessment))
    if (value.assessment_id !== assessmentId || value.session_id !== sessionId || value.item_id !== itemId) throw new StudentError('invalid_response', 502)
    return value
  }
  /** The student's unfinished activity, if any (e.g. after a reload or an unconfirmed start). */
  async activePractice(): Promise<PracticeSession | null> {
    const { value, student } = await this.learning(s => `/v1/students/${s}/practice-sessions`, isPracticeSessionList)
    return value.items.find(item => item.student_id === student && item.status === 'active' && Date.parse(item.budget_ends_at) > Date.now()) ?? null
  }
  async answerPractice(sessionId: string, itemId: string, answer: string | PracticeSubmission): Promise<PracticeAnswer> {
    const submission = typeof answer === 'string' ? { answer_text: answer } : answer
    const trimmed = submission.answer_text.trim()
    if (!trimmed || [...trimmed].length > PRACTICE_ANSWER_MAX) throw new StudentError('answer_length', 422, `请写下答案（不超过 ${PRACTICE_ANSWER_MAX} 个字）。`)
    const selected = submission.selected_options
    const answers = submission.answers?.map(part => ({ field_id: part.field_id, answer_text: part.answer_text.trim() }))
    if (selected && (!selected.length || selected.length > 10 || new Set(selected).size !== selected.length || selected.some(value => !value.trim() || value.length > 8))) throw new StudentError('answer_length', 422, '请重新选择答案。')
    if (answers && (!answers.length || answers.length > 12 || new Set(answers.map(part => part.field_id)).size !== answers.length || answers.some(part => !part.field_id || part.field_id.length > 40 || !part.answer_text))) throw new StudentError('answer_length', 422, '请填好每个空，再提交。')
    const { value } = await this.learning(() => `/v1/practice-sessions/${encoded(sessionId)}/responses`, isPracticeAnswer, { item_id: itemId, answer_text: trimmed, ...(selected ? { selected_options: selected } : {}), ...(answers ? { answers } : {}) })
    if (value.item_id !== itemId) throw new StudentError('invalid_response', 502)
    return value
  }
  async practiceHint(sessionId: string, itemId: string): Promise<PracticeHint> {
    const { value } = await this.learning(() => `/v1/practice-sessions/${encoded(sessionId)}/items/${encoded(itemId)}/hint`, isPracticeHint, {})
    if (value.item_id !== itemId) throw new StudentError('invalid_response', 502)
    return value
  }
  async exitPractice(sessionId: string, reason: ExitReason): Promise<PracticeSession> { return this.owned(await this.learning(() => `/v1/practice-sessions/${encoded(sessionId)}/exit`, isPracticeSession, { reason })) }
}
export const createStudentClient = (options: ClientOptions = {}) => new StudentClient(options)
