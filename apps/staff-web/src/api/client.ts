import type { AssetAccess, AttemptRead, ClaimHelp, CompleteHelp, CorrectionCase, DecisionRead, HelpPage, HelpRequest, Hint, IdentityContext, Problem, QuestionRead, Review, SubmitReview } from './contracts.generated'
import { matchesContract } from './validation'

export class ApiError extends Error {
  constructor(public status: number, message: string, public problem?: Problem) { super(message); this.name = 'ApiError' }
}

export function errorMessage(error: unknown): string {
  if (!(error instanceof ApiError)) return '未能读取服务。请检查网络与服务地址；没有提交成功记录。'
  if (error.message === 'Previous write is unconfirmed; retry the original operation') return '上一笔提交结果尚未确认，不能改内容重新提交。请点击“重试原操作”，沿用原内容和幂等键。'
  const messages: Record<number, string> = {
    0: '无法连接服务或请求已超时。请检查网络、服务地址与跨域配置。',
    401: '授权已失效，请重新连接。当前会话和学生资料已清除。',
    403: '当前身份没有访问或处理权限，不能继续此操作。',
    404: '资源不存在、不可访问，或该接口尚未接通。',
    409: '记录已发生变化或请求冲突。请先刷新，再确认最新内容；不会覆盖他人修改。',
    422: '提交内容不符合接口要求，请检查必填信息。',
    503: '服务暂不可用，请稍后重试。当前没有显示操作完成。',
    502: '服务返回的数据不符合一期契约，已停止显示或提交。',
  }
  return messages[error.status] ?? `服务返回 ${error.status}，操作未确认成功。`
}

export function normalizeBaseUrl(value: string): string {
  const url = new URL(value.trim())
  const loopback = ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname)
  if ((url.protocol !== 'https:' && !(url.protocol === 'http:' && loopback)) || url.username || url.password || url.search || url.hash) {
    throw new Error('请使用 HTTPS 服务地址；本机开发可使用 http://127.0.0.1。不要在地址中放令牌。')
  }
  return url.toString().replace(/\/+$/, '')
}

export class StaffApi {
  private readonly baseUrl: string
  private token: string
  private readonly pendingWrites = new Map<string, { key: string; payload: string; shape: string; requestShape?: string }>()

  constructor(baseUrl: string, token: string, private readonly transport: typeof fetch = fetch) {
    this.baseUrl = normalizeBaseUrl(baseUrl)
    this.token = token.trim()
    if (!this.token) throw new ApiError(401, 'A bearer session is required')
  }
  disconnect() { this.token = ''; this.pendingWrites.clear() }
  hasPendingWrite() { return this.pendingWrites.size > 0 }
  retryPendingWrite() {
    const entry = this.pendingWrites.entries().next().value
    if (!entry) throw new ApiError(409, 'No unresolved write exists')
    const [path, pending] = entry
    return this.request(path, pending.shape, undefined, JSON.parse(pending.payload), pending.requestShape)
  }

  private async request<T>(path: string, shape: string, signal?: AbortSignal, payload?: unknown, requestShape?: string): Promise<T> {
    if (!this.token) throw new ApiError(401, 'A bearer session is required')
    if (requestShape && !matchesContract('requests', requestShape, payload)) throw new ApiError(422, 'Invalid request')
    const headers: Record<string, string> = { Authorization: `Bearer ${this.token}`, Accept: 'application/json, application/problem+json' }
    let write: { key: string; payload: string; shape: string; requestShape?: string } | undefined
    if (payload !== undefined) {
      if (this.pendingWrites.size > 0 && !this.pendingWrites.has(path)) throw new ApiError(409, 'Previous write is unconfirmed; retry the original operation')
      const serialized = JSON.stringify(payload)
      write = this.pendingWrites.get(path)
      if (write && write.payload !== serialized) throw new ApiError(409, 'Previous write is unconfirmed; retry the original operation')
      write ??= { key: crypto.randomUUID(), payload: serialized, shape, requestShape }
      this.pendingWrites.set(path, write)
      headers['Content-Type'] = 'application/json'
      headers['Idempotency-Key'] = write.key
    }
    let response: Response
    try {
      response = await this.transport(`${this.baseUrl}${path}`, {
        method: payload === undefined ? 'GET' : 'POST', headers,
        body: write?.payload, cache: 'no-store', credentials: 'omit', redirect: 'error',
        signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(15000)]) : AbortSignal.timeout(15000),
      })
    } catch (error) {
      if (signal?.aborted) throw error
      throw new ApiError(0, 'Network failure; write acceptance is unknown')
    }
    const value: unknown = await response.json().catch(() => null)
    if (!response.ok) {
      if (payload !== undefined && [401, 403, 404, 409, 422].includes(response.status)) this.pendingWrites.delete(path)
      const problem = matchesContract('data', 'Problem', value) ? value as Problem : undefined
      throw new ApiError(response.status, problem?.title ?? 'Service error', problem)
    }
    if (!matchesContract('data', shape, value)) throw new ApiError(502, 'Response does not match contract')
    this.pendingWrites.delete(path)
    return value as T
  }

  me(signal?: AbortSignal) { return this.request<IdentityContext>('/v1/me', 'IdentityContext', signal) }
  helpQueue(cursor?: string, signal?: AbortSignal) {
    const params = new URLSearchParams({ limit: '30' }); if (cursor) params.set('cursor', cursor)
    return this.request<HelpPage>(`/v1/help-requests?${params}`, 'HelpPage', signal)
  }
  case(id: string, signal?: AbortSignal) { return this.request<CorrectionCase>(`/v1/cases/${encodeURIComponent(id)}`, 'CorrectionCase', signal) }
  question(id: string, signal?: AbortSignal) { return this.request<QuestionRead>(`/v1/questions/${encodeURIComponent(id)}`, 'QuestionRead', signal) }
  attempt(id: string, signal?: AbortSignal) { return this.request<AttemptRead>(`/v1/attempts/${encodeURIComponent(id)}`, 'AttemptRead', signal) }
  decision(id: string, signal?: AbortSignal) { return this.request<DecisionRead>(`/v1/decisions/${encodeURIComponent(id)}`, 'DecisionRead', signal) }
  hint(id: string, signal?: AbortSignal) { return this.request<Hint>(`/v1/hints/${encodeURIComponent(id)}`, 'Hint', signal) }
  asset(id: string, signal?: AbortSignal) { return this.request<AssetAccess>(`/v1/assets/${encodeURIComponent(id)}/access`, 'AssetAccess', signal) }
  claim(help: HelpRequest) { return this.request<HelpRequest>(`/v1/help-requests/${encodeURIComponent(help.id)}/claim`, 'HelpRequest', undefined, { expected_revision: help.revision } satisfies ClaimHelp, 'ClaimHelp') }
  complete(help: HelpRequest, outcome: CompleteHelp['outcome'], note: string) {
    return this.request<HelpRequest>(`/v1/help-requests/${encodeURIComponent(help.id)}/complete`, 'HelpRequest', undefined, { expected_revision: help.revision, outcome, note } satisfies CompleteHelp, 'CompleteHelp')
  }
  review(caseId: string, payload: SubmitReview) { return this.request<Review>(`/v1/cases/${encodeURIComponent(caseId)}/reviews`, 'Review', undefined, payload, 'SubmitReview') }
}
