import type {
  Accepted, AssetAccess, Attempt, AttemptRead, CapturedQuestionRevision, CheckRun, ClaimHelp, CompleteHelp, CorrectionCase, DecisionRead,
  HelpPage, HelpRequest, Hint, HomeworkSession, IdentityContext, ImageAsset, Job, QuestionRead, RepairLayout, ReviseAnswer, ReviseQuestion,
  Review, StartCheck, SubmitReview,
} from './contracts.generated'
import { isAccountInfo, isAuthSession, isReviewQueuePage, type AccountInfo, type AuthSession, type ReviewQueuePage } from './types'
import { matchesContract } from './validation'

export const UNCONFIRMED_WRITE = 'unconfirmed_write'

export class ApiError extends Error {
  constructor(public status: number, message: string, public code?: string, public currentRevision: number | null = null) {
    super(message); this.name = 'ApiError'
  }
}

const codeMessages: Record<string, string> = {
  [UNCONFIRMED_WRITE]: '上一笔提交结果尚未确认，不能改内容或提交其他操作。请点击“重试原操作”，沿用原内容和幂等键。',
  session_expired: '登录已到期，请重新登录。已清除本页的学生资料。',
  invalid_credentials: '用户名或密码不正确，或账号不可用。',
  login_rate_limited: '登录尝试过于频繁，请稍后再试。',
  password_change_required: '请先修改初始密码，再进入工作台。',
  idempotency_conflict: '同一请求键被用于不同内容，请刷新后重新操作。',
  processing_blocked: '这份作业已停止处理（可能正在删除），不能继续操作。',
  recognition_not_ready: '这次作答的识别还没有完成，暂不能复核。',
  confirmation_verdict_mismatch: '确认的结论必须与候选判断一致；如需改判请选择“更正当前判断”。',
  invalid_region: '框选范围必须位于本作业已上传的原图内。',
  asset_unavailable: '所选图片尚未完整上传或不属于本作业。',
  too_many_replacements: '一次最多补充或拆分 12 道题。',
  check_already_started: '相同内容的检查已在进行中，请稍后刷新。',
  unsafe_asset_url: '原图地址不属于当前工作台服务，已拒绝加载。',
  invalid_image: '原图格式不受支持，已停止显示。',
}

export function errorMessage(error: unknown): string {
  if (!(error instanceof ApiError)) return '未能读取服务。请检查网络；没有提交成功记录。'
  if (error.code && codeMessages[error.code]) return codeMessages[error.code]
  const messages: Record<number, string> = {
    0: '无法连接服务或请求已超时。没有显示操作完成。',
    401: '登录已失效，请重新登录。当前会话和学生资料已清除。',
    403: '当前身份没有访问或处理权限，不能继续此操作。',
    404: '资源不存在或不可访问。',
    409: '记录已发生变化（可能是其他老师或孩子刚刚操作）。已重新读取最新内容，请核对后再提交。',
    422: '提交内容不符合接口要求，请检查必填信息。',
    429: '请求过于频繁，请稍后再试。',
    502: '服务返回的数据不符合一期契约，已停止显示或提交。',
    503: '服务暂不可用，请稍后重试。当前没有显示操作完成。',
  }
  return messages[error.status] ?? `服务返回 ${error.status}，操作未确认成功。`
}

export const isConflict = (error: unknown) => error instanceof ApiError && error.status === 409 && error.code !== UNCONFIRMED_WRITE

export function sameOrigin(value: string): string {
  const url = new URL(value)
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.pathname !== '/' || url.search || url.hash) throw new Error('Invalid service origin')
  return url.origin
}

const loopback = (url: URL) => ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname)
const ASSET_CONTENT = /^\/v1\/assets\/[a-z][a-z0-9_-]{2,63}\/content$/
const IMAGE_TYPES = ['image/jpeg', 'image/png', 'image/webp']
const MAX_IMAGE_BYTES = 4 * 1024 * 1024
const segment = encodeURIComponent

interface PendingWrite { key: string; payload: string; shape: string; requestShape?: string }
interface RequestOptions { signal?: AbortSignal; payload?: unknown; requestShape?: string; anonymous?: boolean; keepSessionOn401?: boolean; idempotencyKey?: string }
type Shape = string | ((value: unknown) => boolean)

export interface StaffApiOptions {
  transport?: typeof fetch
  now?: () => number
  /** Dev server only: a loopback read_url is fetched through the same-origin /v1 proxy. */
  proxyLoopbackAssets?: boolean
}

export class StaffApi {
  readonly origin: string
  private token = ''
  private expiresAt = 0
  private readonly pendingWrites = new Map<string, PendingWrite>()
  private readonly pendingAuth = new Map<string, { key: string; payload: string }>()
  private readonly transport: typeof fetch
  private readonly now: () => number
  private readonly proxyLoopbackAssets: boolean
  onSessionEnded: ((reason: 'expired' | 'revoked') => void) | null = null

  constructor(origin: string, options: StaffApiOptions = {}) {
    this.origin = sameOrigin(origin)
    this.transport = options.transport ?? ((...args) => globalThis.fetch(...args))
    this.now = options.now ?? Date.now
    this.proxyLoopbackAssets = options.proxyLoopbackAssets ?? false
  }

  get authenticated() { return !!this.token && this.expiresAt > this.now() }
  get sessionExpiresAt() { return this.token ? this.expiresAt : null }
  hasPendingWrite() { return this.pendingWrites.size > 0 }

  /** Forget the bearer, pending writes and pending sign-ins; nothing is persisted anywhere. */
  clear() { this.token = ''; this.expiresAt = 0; this.pendingWrites.clear(); this.pendingAuth.clear() }

  private endSession(reason: 'expired' | 'revoked') {
    const had = !!this.token
    this.clear()
    if (had) this.onSessionEnded?.(reason)
  }

  private acceptSession(value: AuthSession) {
    const expires = Date.parse(value.expires_at)
    if (expires <= this.now()) throw new ApiError(401, 'Session already expired', 'session_expired')
    this.token = value.access_token; this.expiresAt = expires
  }

  private async request<T>(path: string, shape: Shape, options: RequestOptions = {}): Promise<T> {
    const { signal, payload, requestShape, anonymous = false, idempotencyKey } = options
    if (!anonymous) {
      if (this.token && this.expiresAt <= this.now()) this.endSession('expired')
      if (!this.token) throw new ApiError(401, 'A bearer session is required', 'session_expired')
    }
    if (requestShape && !matchesContract('requests', requestShape, payload)) throw new ApiError(422, 'Invalid request', 'invalid_request')
    const headers: Record<string, string> = { Accept: 'application/json, application/problem+json' }
    if (!anonymous) headers.Authorization = `Bearer ${this.token}`
    const tracked = payload !== undefined && !idempotencyKey
    let serialized: string | undefined
    if (payload !== undefined) {
      serialized = JSON.stringify(payload)
      let key = idempotencyKey
      if (tracked) {
        let write = this.pendingWrites.get(path)
        if ((this.pendingWrites.size > 0 && !write) || (write && write.payload !== serialized)) throw new ApiError(409, 'Previous write is unconfirmed', UNCONFIRMED_WRITE)
        write ??= { key: crypto.randomUUID(), payload: serialized, shape: typeof shape === 'string' ? shape : '', requestShape }
        this.pendingWrites.set(path, write)
        key = write.key
      }
      headers['Content-Type'] = 'application/json'
      headers['Idempotency-Key'] = key!
    }
    const body = await this.send(path, payload === undefined ? 'GET' : 'POST', headers, serialized, signal)
    if (!body.ok) {
      // A definite rejection means nothing was accepted; only unknown outcomes keep the same key for retry.
      if (tracked && body.status >= 400 && body.status < 500) this.pendingWrites.delete(path)
      if (body.status === 401 && !anonymous && !(options.keepSessionOn401 && body.code === 'invalid_credentials')) this.endSession('revoked')
      throw new ApiError(body.status, 'Service error', body.code, body.currentRevision)
    }
    const valid = typeof shape === 'function' ? shape(body.value) : matchesContract('data', shape, body.value)
    if (!valid) throw new ApiError(502, 'Response does not match contract')
    if (tracked) this.pendingWrites.delete(path)
    return body.value as T
  }

  private async send(path: string, method: string, headers: Record<string, string>, body: string | undefined, signal?: AbortSignal) {
    let response: Response
    try {
      response = await this.transport(`${this.origin}${path}`, {
        method, headers, body, cache: 'no-store', credentials: 'omit', redirect: 'error',
        signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(20000)]) : AbortSignal.timeout(20000),
      })
    } catch (error) {
      if (signal?.aborted) throw error
      throw new ApiError(0, 'Network failure; write acceptance is unknown')
    }
    const value: unknown = await response.json().catch(() => null)
    let code: string | undefined
    let currentRevision: number | null = null
    if (!response.ok && value && typeof value === 'object') {
      const problem = value as { code?: unknown; current_revision?: unknown }
      if (typeof problem.code === 'string' && /^[a-z_]{3,64}$/.test(problem.code)) code = problem.code
      if (typeof problem.current_revision === 'number') currentRevision = problem.current_revision
    }
    return { ok: response.ok, status: response.status, value, code, currentRevision }
  }

  // Auth writes keep their own key (a retried sign-in replays the same server session) and never wait on the case-write lock.
  private async authWrite<T>(path: string, payload: unknown, shape: (value: unknown) => boolean, anonymous = false): Promise<T> {
    const serialized = JSON.stringify(payload)
    const previous = this.pendingAuth.get(path)
    const key = previous && previous.payload === serialized ? previous.key : crypto.randomUUID()
    this.pendingAuth.set(path, { key, payload: serialized })
    try {
      const value = await this.request<T>(path, shape, { payload, anonymous, idempotencyKey: key, keepSessionOn401: path === '/v1/auth/change-password' })
      this.pendingAuth.delete(path)
      return value
    } catch (error) {
      if (error instanceof ApiError && error.status >= 400 && error.status < 500) this.pendingAuth.delete(path)
      throw error
    }
  }

  async login(username: string, password: string): Promise<AuthSession> {
    const normalized = username.trim().toLowerCase()
    if (!/^[a-z0-9][a-z0-9._-]{2,63}$/.test(normalized) || !password) throw new ApiError(422, 'Invalid login', 'invalid_credentials')
    this.token = ''; this.expiresAt = 0; this.pendingWrites.clear()
    const session = await this.authWrite<AuthSession>('/v1/auth/login', { username: normalized, password }, isAuthSession, true)
    this.acceptSession(session)
    return session
  }

  async changePassword(currentPassword: string, newPassword: string): Promise<AuthSession> {
    const session = await this.authWrite<AuthSession>('/v1/auth/change-password', { current_password: currentPassword, new_password: newPassword }, isAuthSession)
    this.acceptSession(session)
    return session
  }

  /** Revokes the server session when reachable; the local session is cleared either way. */
  async logout(): Promise<boolean> {
    if (!this.authenticated) { this.clear(); return true }
    try { await this.authWrite('/v1/auth/logout', {}, value => !!value && (value as { logged_out?: unknown }).logged_out === true); return true }
    catch { return false }
    finally { this.clear() }
  }

  account(signal?: AbortSignal) { return this.request<AccountInfo>('/v1/auth/account', isAccountInfo, { signal }) }
  me(signal?: AbortSignal) { return this.request<IdentityContext>('/v1/me', 'IdentityContext', { signal }) }

  helpQueue(cursor?: string, signal?: AbortSignal) {
    const params = new URLSearchParams({ limit: '30' }); if (cursor) params.set('cursor', cursor)
    return this.request<HelpPage>(`/v1/help-requests?${params}`, 'HelpPage', { signal })
  }
  reviewQueue(cursor?: string, signal?: AbortSignal) {
    const params = new URLSearchParams({ limit: '30' }); if (cursor) params.set('cursor', cursor)
    return this.request<ReviewQueuePage>(`/v1/review-queue?${params}`, isReviewQueuePage, { signal })
  }

  case(id: string, signal?: AbortSignal) { return this.request<CorrectionCase>(`/v1/cases/${segment(id)}`, 'CorrectionCase', { signal }) }
  question(id: string, revision?: number, signal?: AbortSignal) {
    return this.request<QuestionRead>(`/v1/questions/${segment(id)}${revision ? `?revision=${revision}` : ''}`, 'QuestionRead', { signal })
  }
  attempt(id: string, signal?: AbortSignal) { return this.request<AttemptRead>(`/v1/attempts/${segment(id)}`, 'AttemptRead', { signal }) }
  decision(id: string, signal?: AbortSignal) { return this.request<DecisionRead>(`/v1/decisions/${segment(id)}`, 'DecisionRead', { signal }) }
  check(id: string, signal?: AbortSignal) { return this.request<CheckRun>(`/v1/checks/${segment(id)}`, 'CheckRun', { signal }) }
  hint(id: string, signal?: AbortSignal) { return this.request<Hint>(`/v1/hints/${segment(id)}`, 'Hint', { signal }) }
  help(id: string, signal?: AbortSignal) { return this.request<HelpRequest>(`/v1/help-requests/${segment(id)}`, 'HelpRequest', { signal }) }
  review(id: string, signal?: AbortSignal) { return this.request<Review>(`/v1/reviews/${segment(id)}`, 'Review', { signal }) }
  homework(id: string, signal?: AbortSignal) { return this.request<HomeworkSession>(`/v1/homeworks/${segment(id)}`, 'HomeworkSession', { signal }) }
  imageAsset(id: string, signal?: AbortSignal) { return this.request<ImageAsset>(`/v1/assets/${segment(id)}`, 'ImageAsset', { signal }) }
  assetAccess(id: string, signal?: AbortSignal) { return this.request<AssetAccess>(`/v1/assets/${segment(id)}/access`, 'AssetAccess', { signal }) }
  job(id: string, signal?: AbortSignal) { return this.request<Job>(`/v1/jobs/${segment(id)}`, 'Job', { signal }) }

  /** Resolve a read_url to a fetchable same-origin URL; foreign origins are refused. */
  assetUrl(readUrl: string): string {
    let url: URL
    try { url = new URL(readUrl, this.origin) } catch { throw new ApiError(502, 'Unsafe asset URL', 'unsafe_asset_url') }
    if (url.username || url.password || url.hash || !ASSET_CONTENT.test(url.pathname)) throw new ApiError(502, 'Unsafe asset URL', 'unsafe_asset_url')
    if (url.origin === this.origin) return url.href
    if (this.proxyLoopbackAssets && loopback(url) && loopback(new URL(this.origin))) return `${this.origin}${url.pathname}${url.search}`
    throw new ApiError(502, 'Unsafe asset URL', 'unsafe_asset_url')
  }

  async image(readUrl: string, signal?: AbortSignal): Promise<Blob> {
    const target = this.assetUrl(readUrl)
    let response: Response
    try { response = await this.transport(target, { cache: 'no-store', credentials: 'omit', redirect: 'error', signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(30000)]) : AbortSignal.timeout(30000) }) }
    catch (error) { if (signal?.aborted) throw error; throw new ApiError(0, 'Image unavailable') }
    if (!response.ok) throw new ApiError(response.status, 'Image unavailable')
    const mime = response.headers.get('content-type')?.split(';')[0].trim() ?? ''
    if (!IMAGE_TYPES.includes(mime)) throw new ApiError(502, 'Invalid image', 'invalid_image')
    const bytes = await response.arrayBuffer()
    if (bytes.byteLength > MAX_IMAGE_BYTES) throw new ApiError(502, 'Invalid image', 'invalid_image')
    return new Blob([bytes], { type: mime })
  }

  /** Re-send the unconfirmed write with its original key and payload. */
  async retryPendingWrite(): Promise<{ path: string; value: unknown }> {
    const entry = this.pendingWrites.entries().next().value
    if (!entry) throw new ApiError(409, 'No unresolved write exists', 'no_pending_write')
    const [path, pending] = entry
    const value = await this.request(path, pending.shape, { payload: JSON.parse(pending.payload), requestShape: pending.requestShape })
    return { path, value }
  }

  private write<T>(path: string, shape: string, requestShape: string, payload: unknown) {
    return this.request<T>(path, shape, { payload, requestShape })
  }

  claim(help: HelpRequest) {
    return this.write<HelpRequest>(`/v1/help-requests/${segment(help.id)}/claim`, 'HelpRequest', 'ClaimHelp', { expected_revision: help.revision } satisfies ClaimHelp)
  }
  complete(help: HelpRequest, outcome: CompleteHelp['outcome'], note: string) {
    return this.write<HelpRequest>(`/v1/help-requests/${segment(help.id)}/complete`, 'HelpRequest', 'CompleteHelp', { expected_revision: help.revision, outcome, note } satisfies CompleteHelp)
  }
  submitReview(caseId: string, payload: SubmitReview) { return this.write<Review>(`/v1/cases/${segment(caseId)}/reviews`, 'Review', 'SubmitReview', payload) }
  startCheck(caseId: string, payload: StartCheck) { return this.write<Accepted>(`/v1/cases/${segment(caseId)}/checks`, 'Accepted', 'StartCheck', payload) }
  reviseQuestion(questionId: string, payload: ReviseQuestion) {
    return this.write<CapturedQuestionRevision>(`/v1/questions/${segment(questionId)}/revisions`, 'CapturedQuestionRevision', 'ReviseQuestion', payload)
  }
  reviseAnswer(attemptId: string, payload: ReviseAnswer) {
    return this.write<Attempt>(`/v1/attempts/${segment(attemptId)}/recognition-revisions`, 'Attempt', 'ReviseAnswer', payload)
  }
  repairLayout(homeworkId: string, payload: RepairLayout) {
    return this.write<Accepted>(`/v1/homeworks/${segment(homeworkId)}/layout-revisions`, 'Accepted', 'RepairLayout', payload)
  }
}
