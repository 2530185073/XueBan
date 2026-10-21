import { afterEach, describe, expect, it, vi } from 'vitest'
import { RecognitionRecoveryController, type RecoveryClient, type RecoveryView } from './recognitionRecovery'
import { StudentError } from './errors'
import type { CaseDetail, RecognitionRecoveryStatus } from './types'
const row = { case: { id: 'case_one' }, attempt: { id: 'attempt_one' } } as CaseDetail
const available: RecognitionRecoveryStatus = { case_id: 'case_one', attempt_id: 'attempt_one', used: 0, remaining: 2, limit: 2, eligible: true, blocked_reason: null, active_job_id: null, last_job_id: null, last_outcome: null }
const running: RecognitionRecoveryStatus = { ...available, used: 1, remaining: 1, eligible: false, blocked_reason: 'active_recovery', active_job_id: 'job_one', last_job_id: 'job_one' }
const finished: RecognitionRecoveryStatus = { ...available, used: 1, remaining: 1, last_job_id: 'job_one', last_outcome: 'unchanged_pending' }
const deferred = <T>() => { let resolve!: (value: T) => void; const promise = new Promise<T>(r => { resolve = r }); return { promise, resolve } }
afterEach(() => vi.useRealTimers())
describe('photo-bound read-only recovery observation', () => {
  it('restores a running job, polls only status and refreshes the same record once on completion', async () => {
    vi.useFakeTimers()
    const client = { recognitionRecoveryStatus: vi.fn().mockResolvedValueOnce(running).mockResolvedValue(finished), startRecognitionRecheck: vi.fn() }
    const emit = vi.fn(), updated = vi.fn(async () => {})
    const c = new RecognitionRecoveryController(client, row, emit, updated)
    await c.load(); expect(emit.mock.lastCall?.[0].status.active_job_id).toBe('job_one')
    await vi.advanceTimersByTimeAsync(2500)
    expect(updated).toHaveBeenCalledTimes(1); expect(client.startRecognitionRecheck).not.toHaveBeenCalled()
    await c.load(); expect(updated).toHaveBeenCalledTimes(1); c.dispose()
  })
  it.each(['applied', 'unchanged_pending', 'service_failed'] as const)('syncs an initial terminal %s once even when the active phase happened offline', async last_outcome => {
    const terminal = { ...finished, last_outcome }
    const client = { recognitionRecoveryStatus: vi.fn().mockResolvedValue(terminal), startRecognitionRecheck: vi.fn() }
    const updated = vi.fn(async () => {})
    const c = new RecognitionRecoveryController(client, row, () => {}, updated)
    await c.load(); await c.load(); await c.load()
    expect(updated).toHaveBeenCalledTimes(1)
    expect(client.startRecognitionRecheck).not.toHaveBeenCalled(); c.dispose()
  })
  it('allows a failed initial cache sync to retry, without treating the completed job as newly submitted', async () => {
    const client = { recognitionRecoveryStatus: vi.fn().mockResolvedValue(finished), startRecognitionRecheck: vi.fn() }
    const updated = vi.fn<(_: () => boolean) => Promise<void>>().mockRejectedValueOnce(new StudentError('network_unknown')).mockResolvedValue(undefined)
    const c = new RecognitionRecoveryController(client, row, () => {}, updated)
    await c.load(); await c.load(); await c.load()
    expect(updated).toHaveBeenCalledTimes(2); expect(client.startRecognitionRecheck).not.toHaveBeenCalled(); c.dispose()
  })
  it('reads status after a failed submission without auto-retrying a paid request', async () => {
    const client = { recognitionRecoveryStatus: vi.fn().mockResolvedValueOnce(available).mockResolvedValue(running), startRecognitionRecheck: vi.fn().mockRejectedValue(new StudentError('http_503', 503)) }
    let view: RecoveryView | undefined
    const c = new RecognitionRecoveryController(client, row, v => { view = v }, async () => {})
    await c.load(); await c.start()
    expect(view!.status!.active_job_id).toBe('job_one'); expect(view!.error).toBeNull()
    expect(client.startRecognitionRecheck).toHaveBeenCalledTimes(1); c.dispose()
  })
  it('keeps a terminal service failure distinct from a correct result and obeys exhausted budget', async () => {
    const exhausted = { ...finished, used: 2, remaining: 0, eligible: false, blocked_reason: 'budget_exhausted', last_outcome: 'service_failed' }
    const client = { recognitionRecoveryStatus: vi.fn().mockResolvedValue(exhausted), startRecognitionRecheck: vi.fn() }
    const emit = vi.fn(), c = new RecognitionRecoveryController(client, row, emit, async () => {})
    await c.load(); await c.start()
    expect(emit.mock.lastCall?.[0].status.last_outcome).toBe('service_failed'); expect(client.startRecognitionRecheck).not.toHaveBeenCalled(); c.dispose()
  })
  it.each(['read', 'submit'])('ignores late %s responses after switching photo/attempt', async stage => {
    const wait = deferred<RecognitionRecoveryStatus>(), emit = vi.fn(), updated = vi.fn(async () => {})
    const client: RecoveryClient = { recognitionRecoveryStatus: vi.fn().mockResolvedValue(available), startRecognitionRecheck: vi.fn(() => wait.promise) }
    const c = new RecognitionRecoveryController(client, row, emit, updated)
    await c.load()
    if (stage === 'read') client.recognitionRecoveryStatus = () => wait.promise
    const pending = stage === 'read' ? c.load() : c.start()
    c.dispose(); const calls = emit.mock.calls.length
    wait.resolve(finished); await pending
    expect(emit).toHaveBeenCalledTimes(calls); expect(updated).not.toHaveBeenCalled()
  })
  it('refreshes changed versions after a 409 even when there is no previous recovery job', async () => {
    const client = { recognitionRecoveryStatus: vi.fn().mockResolvedValue(available), startRecognitionRecheck: vi.fn().mockRejectedValue(new StudentError('revision_conflict', 409)) }
    const updated = vi.fn(async () => {})
    const c = new RecognitionRecoveryController(client, row, () => {}, updated)
    await c.load(); await c.start()
    expect(updated).toHaveBeenCalledTimes(1); expect(client.startRecognitionRecheck).toHaveBeenCalledTimes(1); c.dispose()
  })
  it('gives a completion refresh a live guard so switching photos during its read cannot apply stale data', async () => {
    const gate = deferred<void>(), applied = vi.fn()
    const client = { recognitionRecoveryStatus: vi.fn().mockResolvedValueOnce(running).mockResolvedValue(finished), startRecognitionRecheck: vi.fn() }
    const c = new RecognitionRecoveryController(client, row, () => {}, async current => { await gate.promise; if (current()) applied() })
    await c.load(); const completed = c.load(); await Promise.resolve(); c.dispose(); gate.resolve(); await completed
    expect(applied).not.toHaveBeenCalled()
  })
  it('prevents repeated clicks while submitting and never starts without a server budget', async () => {
    const wait = deferred<RecognitionRecoveryStatus>()
    const client = { recognitionRecoveryStatus: vi.fn().mockResolvedValue(available), startRecognitionRecheck: vi.fn(() => wait.promise) }
    const c = new RecognitionRecoveryController(client, row, () => {}, async () => {})
    await c.start(); expect(client.startRecognitionRecheck).not.toHaveBeenCalled()
    await c.load(); const first = c.start(); await c.start(); expect(client.startRecognitionRecheck).toHaveBeenCalledTimes(1)
    wait.resolve(running); await first; c.dispose()
  })
})
