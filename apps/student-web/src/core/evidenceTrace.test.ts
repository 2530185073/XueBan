import { expect, it, vi } from 'vitest'
import { IDBFactory } from 'fake-indexeddb'
import examples from '../../../../contracts/phase1/examples.json'
import { EvidenceTraceUnavailable, loadEvidenceTrace, resolveEvidence } from './evidenceTrace'
import { traceFixture } from './evidenceTrace.fixtures'
import { StudentClient } from './student'
import { DraftStore } from './store'
function setup() {
  const f = traceFixture()
  const client = { evidenceScope: 'scope_synthetic', knowledgeNode: vi.fn(async () => structuredClone(f.detail)), caseRecord: vi.fn(async () => structuredClone(f.record)), questionRevision: vi.fn(async (_hw: string, _q: string, revision: number) => ({ record: structuredClone(f.questions[revision - 1]), current_revision: 2, currently_effective: revision === 2 })) }
  return { ...f, client }
}
it('resolves two candidate decisions of one case to exact distinct answers, photos and captured questions', async () => {
  const s = setup(), before = structuredClone(s.record), old = await loadEvidenceTrace(s.client, s.focus(), s.studentId), corrected = await loadEvidenceTrace(s.client, s.focus(1), s.studentId)
  expect(old.attempt.answer_text).toBe('(-18) / 9 = 2'); expect(old.attempt.asset_ids).toEqual(['asset_original']); expect(old.question.revision).toBe(1)
  expect(corrected.attempt.answer_text).toContain('= -2'); expect(corrected.attempt.asset_ids).toEqual(['asset_correction']); expect(corrected.question.revision).toBe(2)
  expect(s.record).toEqual(before); expect(s.client.questionRevision.mock.calls.map(x => x[2])).toEqual([1, 2])
})
it('permits historical stale judgment only while that exact attempt has live evidence', () => {
  const s = setup(); s.record.decisions[0].record.validity = 'stale'
  expect(resolveEvidence(s.focus(), s.studentId, s.detail, s.record).attempt.id).toBe('attempt_original')
  const focus = s.focus(); s.detail.evidence.shift()
  expect(() => resolveEvidence(focus, s.studentId, s.detail, s.record)).toThrow(EvidenceTraceUnavailable)
})
const cases: [string, (s: ReturnType<typeof setup>) => void][] = [
  ['removed evidence', s => { s.detail.evidence.shift() }], ['graph switch', s => { s.detail.graph_version = 'graph_other' }], ['projection pending', s => { s.detail.projection!.pending = true }],
  ['retired case', s => { s.record.case.lifecycle = 'superseded' }], ['other student', s => { s.record.case.scope.student_id = 'student_other' }], ['other homework', s => { s.record.case.homework_id = 'homework_other' }],
  ['missing attempt', s => { s.record.attempts.shift() }], ['duplicate attempt', s => { s.record.attempts.push(structuredClone(s.record.attempts[0])) }],
  ['wrong decision', s => { s.record.decisions[0].record.input_versions.attempt_id = 'attempt_correction' }], ['changed reading', s => { s.record.attempts[0].record.recognition_revision++ }],
  ['retracted decision', s => { s.record.decisions[0].record.validity = 'retracted' }], ['old wrapper', s => { s.record.attempts[0].current_revision++ }], ['missing old pointers', s => { s.detail.evidence[0].attempt_id = undefined }],
]
it.each(cases)('never substitutes current answer for %s', async (_name, mutate) => {
  const s = setup(), focus = s.focus(); mutate(s)
  await expect(loadEvidenceTrace(s.client, focus, s.studentId)).rejects.toBeInstanceOf(EvidenceTraceUnavailable); expect(s.client.questionRevision).not.toHaveBeenCalled()
})
it.each(['scope', 'graph', 'reading'] as const)('rejects %s change while the historical question loads', async kind => {
  const s = setup()
  s.client.questionRevision.mockImplementationOnce(async () => {
    if (kind === 'scope') s.client.evidenceScope = 'new_scope'
    if (kind === 'graph') s.detail.graph_version = 'graph_new'
    if (kind === 'reading') s.record.attempts[0].record.recognition_revision++
    return { record: s.questions[0], current_revision: 2, currently_effective: false }
  })
  await expect(loadEvidenceTrace(s.client, s.focus(), s.studentId)).rejects.toBeInstanceOf(EvidenceTraceUnavailable)
})
it('does not accept the newer question in place of the exact captured revision', async () => {
  const s = setup(); s.client.questionRevision.mockResolvedValueOnce({ record: s.questions[1], current_revision: 2, currently_effective: true })
  await expect(loadEvidenceTrace(s.client, s.focus(), s.studentId)).rejects.toBeInstanceOf(EvidenceTraceUnavailable)
})
class MemorySession implements Storage {
  values = new Map<string, string>(); get length() { return this.values.size }
  clear() { this.values.clear() }; getItem(k: string) { return this.values.get(k) ?? null }; key(i: number) { return [...this.values.keys()][i] ?? null }; removeItem(k: string) { this.values.delete(k) }; setItem(k: string, v: string) { this.values.set(k, v) }
}
const json = (value: unknown) => new Response(JSON.stringify(value), { headers: { 'Content-Type': 'application/json' } })
function realClient() {
  const f = traceFixture(), storage = new MemorySession(), calls: string[] = [], homework = structuredClone(examples.entries.find(x => x.name === 'homework')!.value) as Record<string, unknown>
  Object.assign(homework, { id: 'homework_synthetic', scope: f.record.case.scope, question_ids: ['question_synthetic'] })
  let question = async () => json({ record: f.questions[0], current_revision: 2, currently_effective: false })
  const client = new StudentClient({ origin: 'http://localhost', storage, store: new DraftStore(new IDBFactory()), transport: async (input, init) => {
    const url = new URL(String(input)); calls.push(url.pathname + url.search)
    if (url.pathname === '/v1/auth/login') return json({ access_token: 'synthetic-token', expires_at: '2099-01-01T00:00:00Z', must_change_password: false })
    if (url.pathname === '/v1/auth/account') return json({ username: 'synthetic', student_id: f.studentId, must_change_password: false })
    if (url.pathname === '/v1/me') return json({ actor_id: 'actor_synthetic', roles: ['student'], authorized_students: [{ student_id: f.studentId, campus_id: 'campus_synthetic', display_name: '合成学生' }] })
    if (url.pathname === '/v1/auth/logout') return json({ logged_out: true })
    if (init?.method === 'POST') throw new Error('No business writes')
    if (url.pathname === '/v1/homeworks/homework_synthetic') return json(homework)
    if (url.pathname === '/v1/questions/question_synthetic') return question()
    throw new Error('Unexpected request')
  } })
  return { client, calls, homework, setQuestion: (next: typeof question) => { question = next } }
}
it('reads historical ineffective QuestionRead through exact authorized revision URL', async () => {
  const s = realClient(); await s.client.login('synthetic', 'synthetic')
  expect((await s.client.questionRevision('homework_synthetic', 'question_synthetic', 1)).record.revision).toBe(1); expect(s.calls).toContain('/v1/questions/question_synthetic?revision=1')
  s.homework.scope = { tenant_id: 'tenant_synthetic', campus_id: 'campus_synthetic', student_id: 'student_other' }
  await expect(s.client.questionRevision('homework_synthetic', 'question_synthetic', 1)).rejects.toBeDefined()
})
it('same-token restore changes trace scope and rejects an old session success', async () => {
  const s = realClient(); await s.client.login('synthetic', 'synthetic'); const scope = s.client.evidenceScope, history = s.client.historyScope
  let entered!: () => void, finish!: () => void
  const started = new Promise<void>(r => { entered = r }), wait = new Promise<void>(r => { finish = r })
  s.setQuestion(async () => { entered(); await wait; return json({ record: traceFixture().questions[0], current_revision: 2, currently_effective: false }) })
  const pending = s.client.questionRevision('homework_synthetic', 'question_synthetic', 1), assertion = expect(pending).rejects.toMatchObject({ code: 'session_changed' })
  await started; await s.client.restore(); expect(s.client.historyScope).toBe(history); expect(s.client.evidenceScope).not.toBe(scope); finish(); await assertion
})
