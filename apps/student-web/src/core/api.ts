import { StudentError } from './errors'
import { valid } from './validation'

const accountErrorStatus: Record<string, number> = { invalid_credentials: 401, login_rate_limited: 429, password_change_required: 403, username_conflict: 409 }
// Codes of the later-phase learning endpoints that the phase-one Problem enum does not list.
export const learningErrorStatus: Record<string, number> = { forbidden: 403, optional_gate_closed: 409, practice_not_needed: 409, course_unknown: 409, curriculum_mismatch: 409, subject_already_recognized: 409, no_reviewed_items: 409, hints_disabled: 409, practice_active: 409, unsupported_source_type: 409, stale_source: 409, source_not_eligible: 409, generation_not_ready: 409, assessment_pending: 409 }

const recoveryErrorCodes = new Set(['active', 'active_recovery', 'budget_exhausted', 'already_resolved', 'staff_confirmed', 'inactive_case', 'initial_recognition_pending', 'multiple_source_pages'].map(reason => `recognition_recheck_${reason}`))

export class StudentApi {
  constructor(readonly origin: string, private token: () => string | null, private revoked: () => void, private transport: typeof fetch = (...args) => globalThis.fetch(...args), private captureSessionFence: () => (() => boolean) = () => () => true) {}
  private async bounded(response: Response, maximum = 2_000_000): Promise<Uint8Array> {
    const reader = response.body?.getReader(); if (!reader) throw new StudentError('invalid_response', 502)
    const parts: Uint8Array[] = []; let size = 0
    try { while (true) { const part = await reader.read(); if (part.done) break; size += part.value.length; if (size > maximum) throw new StudentError('response_too_large', 502); parts.push(part.value) } }
    finally { await reader.cancel().catch(() => undefined) }
    const output = new Uint8Array(size); let offset = 0; for (const part of parts) { output.set(part, offset); offset += part.length } return output
  }
  async request<T>(path: string, shape: string, body?: string, key?: string, publicRequest = false, method?: 'PUT', onUnauthorized: () => void = this.revoked): Promise<T> {
    if (!path.startsWith('/v1/') || path.split('?')[0].includes('..') || path.includes('#') || path.includes('\\')) throw new StudentError('invalid_path', 422)
    const stillCurrent = this.captureSessionFence()
    const token = this.token(); if (!publicRequest && !token) throw new StudentError('authentication_required', 401)
    const headers: Record<string, string> = { Accept: 'application/json' }
    if (!publicRequest) headers.Authorization = `Bearer ${token}`
    if (body !== undefined && !key) throw new StudentError('missing_idempotency_key', 422)
    if (body !== undefined) { headers['Content-Type'] = 'application/json'; headers['Idempotency-Key'] = key! }
    let response: Response
    try { response = await this.transport(this.origin + path, { method: method ?? (body === undefined ? 'GET' : 'POST'), headers, body, cache: 'no-store', credentials: 'omit', redirect: 'error', signal: AbortSignal.timeout(30_000) }) }
    catch { throw new StudentError('network_unknown') }
    if (!response.ok) {
      let code = `http_${response.status}`
      try { const problem = JSON.parse(new TextDecoder().decode(await this.bounded(response))) as { code: string; status: number }; if (problem && problem.status === response.status && (valid('Problem', problem) || (path.startsWith('/v1/auth/') || path.startsWith('/v1/admin/')) && accountErrorStatus[problem.code] === response.status || shape === 'Learning' && learningErrorStatus[problem.code] === response.status || shape === 'RecognitionRecheckAccepted' && response.status === 409 && recoveryErrorCodes.has(problem.code))) code = problem.code } catch { /* No provider/body text escapes to the UI. */ }
      // A mistyped current password does not revoke an otherwise valid session.
      if (response.status === 401 && !publicRequest && !(path === '/v1/auth/change-password' && code === 'invalid_credentials') && stillCurrent()) onUnauthorized()
      throw new StudentError(code, response.status)
    }
    let value: unknown
    try { value = JSON.parse(new TextDecoder().decode(await this.bounded(response))) } catch { throw new StudentError('invalid_response', 502) }
    if (shape === 'AuthSession') {
      const p = value as { access_token?: unknown; expires_at?: unknown; must_change_password?: unknown }
      if (!p || typeof p.access_token !== 'string' || !p.access_token || p.access_token.length > 16384 || typeof p.expires_at !== 'string' || !Number.isFinite(Date.parse(p.expires_at)) || typeof p.must_change_password !== 'boolean') throw new StudentError('invalid_response', 502)
    } else if (shape === 'Account') {
      const p = value as { username?: unknown; must_change_password?: unknown; student_id?: unknown }
      if (!p || typeof p.username !== 'string' || !p.username || p.username.length > 64 || typeof p.must_change_password !== 'boolean' || !(p.student_id === null || typeof p.student_id === 'string' && !!p.student_id && p.student_id.length <= 200)) throw new StudentError('invalid_response', 502)
    } else if (shape === 'LoggedOut') {
      if (!value || (value as { logged_out?: unknown }).logged_out !== true) throw new StudentError('invalid_response', 502)
    } else if (shape === 'AdminResponse' || shape === 'Learning') {
      // Admin components and core/learning.ts validate their bounded, independently versioned DTOs.
      if (!value || typeof value !== 'object' || Array.isArray(value)) throw new StudentError('invalid_response', 502)
    } else if (!valid(shape, value)) throw new StudentError('invalid_response', 502)
    return value as T
  }
  storageUrl(value: string) { const url = new URL(value, this.origin); if (url.origin !== this.origin || url.username || url.password || url.hash || !/^\/v1\/assets\/[a-z0-9_-]+\/content$/.test(url.pathname)) throw new StudentError('unsafe_asset_url', 502); return url.href }
  async upload(url: string, headers: Record<string, string>, blob: Blob) {
    for (const [name, value] of Object.entries(headers)) if (!['x-upload-token', 'content-type'].includes(name.toLowerCase()) || typeof value !== 'string') throw new StudentError('unsafe_upload_header', 502)
    let response: Response
    try { response = await this.transport(this.storageUrl(url), { method: 'PUT', headers, body: blob, redirect: 'error', credentials: 'omit', signal: AbortSignal.timeout(45_000) }) } catch (e) { if (e instanceof StudentError) throw e; throw new StudentError('upload_unknown') }
    if (!response.ok) throw new StudentError('upload_failed', response.status)
  }
  async image(url: string): Promise<Blob> {
    let response: Response
    try { response = await this.transport(this.storageUrl(url), { redirect: 'error', credentials: 'omit', cache: 'no-store', signal: AbortSignal.timeout(30_000) }) } catch (e) { if (e instanceof StudentError) throw e; throw new StudentError('image_unavailable') }
    if (!response.ok) throw new StudentError('image_unavailable', response.status)
    const mime = response.headers.get('content-type')?.split(';')[0] ?? ''
    if (!['image/jpeg', 'image/png', 'image/webp'].includes(mime)) throw new StudentError('invalid_image', 502)
    const bytes = await this.bounded(response, 4_194_304); return new Blob([bytes.buffer as ArrayBuffer], { type: mime })
  }
}
