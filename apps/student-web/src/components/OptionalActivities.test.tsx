import { describe, expect, it, vi } from 'vitest'
import { StudentError } from '../core/errors'
import type { PracticeSession } from '../core/learning'
import { optionalEntryVisible, startOptionalActivity } from './OptionalActivities'

const detail = { cases: [] }
const base = { page: 'work', draft: { stage: 'complete' as const }, detail, busy: '', evidenceActive: false }
describe('optional entry placement', () => {
  it('shows under a finished result page, which the old !detail rule hid', () => {
    expect(optionalEntryVisible(base)).toBe(true)
  })
  it('still shows on the empty start page and on a history result without a local draft', () => {
    expect(optionalEntryVisible({ ...base, draft: null, detail: null })).toBe(true)
    expect(optionalEntryVisible({ ...base, draft: null })).toBe(true)
  })
  it.each(['local', 'creating', 'uploading', 'recognizing', 'checking', 'waiting', 'failed'] as const)('stays hidden while a draft is %s', stage => {
    expect(optionalEntryVisible({ ...base, draft: { stage } })).toBe(false)
  })
  it('stays hidden over a complete draft that has no loaded result yet', () => {
    expect(optionalEntryVisible({ ...base, detail: null })).toBe(false)
  })
  it('is hidden on other pages, while work is in flight, and over the read-only history panel', () => {
    for (const page of ['history', 'knowledge', 'variants', 'practice', 'me', 'password']) expect(optionalEntryVisible({ ...base, page })).toBe(false)
    expect(optionalEntryVisible({ ...base, busy: '正在检查作业' })).toBe(false)
    expect(optionalEntryVisible({ ...base, evidenceActive: true })).toBe(false)
  })
})

const session = { id: 'practice_1' } as unknown as PracticeSession
const request = { purpose: 'preview', willing: true, with_quiz: false } as const
function harness(overrides: Partial<Parameters<typeof startOptionalActivity>[0]['client']> = {}, live = { value: true }) {
  const calls = { started: vi.fn(), failed: vi.fn(), reload: vi.fn(async () => {}) }
  const client = { startPractice: vi.fn(async () => session), activePractice: vi.fn(async () => null), ...overrides }
  const run = () => startOptionalActivity({ client, request, current: () => live.value, onStarted: calls.started, onFailed: calls.failed, reload: calls.reload })
  return { client, calls, run, live }
}
describe('starting an optional activity', () => {
  it('opens the new session when the view is still current', async () => {
    const h = harness(); await h.run()
    expect(h.calls.started).toHaveBeenCalledWith(session); expect(h.client.startPractice).toHaveBeenCalledTimes(1)
  })
  it('drops a late reply after the student left, switched account or restored the session', async () => {
    const live = { value: true }
    const h = harness({ startPractice: vi.fn(async () => { live.value = false; return session }) }, live); await h.run()
    expect(h.calls.started).not.toHaveBeenCalled(); expect(h.calls.failed).not.toHaveBeenCalled()
  })
  it('resumes the active session after an unconfirmed start instead of starting another', async () => {
    const active = { id: 'practice_active' } as unknown as PracticeSession
    const h = harness({ startPractice: vi.fn(async () => { throw new StudentError('network_unknown', 0) }), activePractice: vi.fn(async () => active) }); await h.run()
    expect(h.client.startPractice).toHaveBeenCalledTimes(1); expect(h.calls.started).toHaveBeenCalledWith(active, expect.any(String))
  })
  it('does not reopen a stale active session found after the view changed', async () => {
    const live = { value: true }, active = { id: 'practice_active' } as unknown as PracticeSession
    const h = harness({ startPractice: vi.fn(async () => { throw new StudentError('network_unknown', 0) }),
      activePractice: vi.fn(async () => { live.value = false; return active }) }, live); await h.run()
    expect(h.calls.started).not.toHaveBeenCalled(); expect(h.calls.reload).not.toHaveBeenCalled()
  })
  it('reports a plain failure and reloads the gate without starting anything', async () => {
    const h = harness({ startPractice: vi.fn(async () => { throw new StudentError('server_error', 500) }) }); await h.run()
    expect(h.calls.started).not.toHaveBeenCalled(); expect(h.calls.failed).toHaveBeenCalledTimes(1); expect(h.calls.reload).toHaveBeenCalledTimes(1)
  })
})
