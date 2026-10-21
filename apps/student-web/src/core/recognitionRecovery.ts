import { safeError, StudentError } from './errors'
import type { CaseDetail, RecognitionRecoveryStatus } from './types'

export interface RecoveryClient {
  recognitionRecoveryStatus(caseId: string, attemptId: string): Promise<RecognitionRecoveryStatus>
  startRecognitionRecheck(row: CaseDetail): Promise<RecognitionRecoveryStatus>
}
export interface RecoveryView { status: RecognitionRecoveryStatus | null; loading: boolean; submitting: boolean; error: string | null }
export const initialRecoveryView: RecoveryView = { status: null, loading: true, submitting: false, error: null }
/** One mounted photo/attempt. Timers only GET; disposing fences every late response. */
export class RecognitionRecoveryController {
  private live = true
  private sequence = 0
  private timer: ReturnType<typeof setTimeout> | undefined
  private completed = new Set<string>()
  private view: RecoveryView = { ...initialRecoveryView }
  constructor(private client: RecoveryClient, private row: CaseDetail, private emit: (view: RecoveryView) => void, private updated: (stillCurrent: () => boolean) => Promise<void>, private failed: (error: unknown) => void = () => {}) {}
  private set(patch: Partial<RecoveryView>) { if (this.live) { this.view = { ...this.view, ...patch }; this.emit(this.view) } }
  private schedule() { clearTimeout(this.timer); if (this.live && this.view.status?.active_job_id) this.timer = setTimeout(() => { void this.load() }, 2500) }
  private async refreshStale(error: unknown) {
    if (this.live && error instanceof StudentError && ['revision_conflict', 'stale_input', 'stale_detail'].includes(error.code)) await this.updated(() => this.live)
  }
  private async accept(status: RecognitionRecoveryStatus) {
    this.set({ status, error: null })
    // A reload/offline cache may never have observed the active phase. Sync a
    // terminal job once per observer, including its first GET. Persisted row
    // revisions keep the resulting render stable instead of remount-refreshing.
    if (!status.active_job_id && status.last_job_id && status.last_outcome && !this.completed.has(status.last_job_id)) {
      await this.updated(() => this.live)
      if (this.live) this.completed.add(status.last_job_id)
    }
    this.schedule()
  }
  async load() {
    if (!this.live || this.view.submitting) return
    const sequence = ++this.sequence
    this.set({ loading: true })
    try {
      const status = await this.client.recognitionRecoveryStatus(this.row.case.id, this.row.attempt.id)
      if (!this.live || sequence !== this.sequence) return
      await this.accept(status)
    } catch (error) { if (this.live && sequence === this.sequence) { this.set({ error: safeError(error).message }); this.failed(error); await this.refreshStale(error).catch(() => {}); this.schedule() } }
    finally { if (this.live && sequence === this.sequence) this.set({ loading: false }) }
  }
  async start() {
    if (!this.live || this.view.submitting || !this.view.status?.eligible || this.view.status.active_job_id) return
    const sequence = ++this.sequence; clearTimeout(this.timer)
    this.set({ submitting: true, error: null })
    try {
      const status = await this.client.startRecognitionRecheck(this.row)
      if (!this.live || sequence !== this.sequence) return
      await this.accept(status)
    } catch (error) {
      if (!this.live || sequence !== this.sequence) return
      this.failed(error)
      await this.refreshStale(error).catch(() => {})
      if (!this.live || sequence !== this.sequence) return
      // Unknown submission is reconciled by read-only status; never auto-resubmit.
      try {
        const status = await this.client.recognitionRecoveryStatus(this.row.case.id, this.row.attempt.id)
        if (!this.live || sequence !== this.sequence) return
        await this.accept(status)
        if (!status.active_job_id) this.set({ error: safeError(error).message })
      } catch { if (this.live && sequence === this.sequence) this.set({ error: safeError(error).message }) }
    } finally { if (this.live && sequence === this.sequence) this.set({ submitting: false, loading: false }) }
  }
  dispose() { this.live = false; ++this.sequence; clearTimeout(this.timer) }
}
