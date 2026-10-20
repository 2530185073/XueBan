import { describe, expect, it, vi } from 'vitest'
import { IDBFactory } from 'fake-indexeddb'
import { StudentClient } from './student'
import { DraftStore } from './store'
import { StudentError } from './errors'
import { isAgentContext, isKnowledgeDetail, isKnowledgeMap, isMemoryList, isOptionalActivities, isPracticeAnswer, isPracticeSession, isVariantPracticeOverview } from './learning'
import { activities, context, detail, map, memory, session, state, variantOverview } from './learning.fixtures'

describe('learning response guards', () => {
  it('accept the backend shapes', () => {
    expect(isKnowledgeMap(map)).toBe(true); expect(isKnowledgeDetail(detail)).toBe(true); expect(isAgentContext(context)).toBe(true)
    expect(isMemoryList({ student_id: 'student_one', items: [memory], notice: 'n' })).toBe(true); expect(isOptionalActivities(activities)).toBe(true)
    expect(isVariantPracticeOverview(variantOverview)).toBe(true)
    expect(isOptionalActivities({ ...activities, gate: { open: false, reasons: ['no_extra_budget'], remaining_minutes: 0 }, variants: [], preview: null })).toBe(true)
    expect(isPracticeSession(session)).toBe(true)
    expect(isPracticeSession({ ...session, purpose: 'preview', items: [], hints_allowed: false, targets: [{ id: 'kp-x', name: '去括号', objective: null, prerequisites: [{ id: 'kp-y', name: '合并同类项', learning_status: 'consolidating' }] }] })).toBe(true)
    expect(isPracticeAnswer({ item_id: 'item_1', result: 'unscorable', solution_text: '2 + 3 = 5', session_status: 'completed' })).toBe(true)
  })
  it('reject unknown statuses, wrong types and missing fields', () => {
    expect(isKnowledgeMap({ ...map, states: [{ ...state, learning_status: 'mastered' }] })).toBe(false)
    expect(isKnowledgeMap({ ...map, chapters: [{ ...map.chapters[0], status_counts: { percent: 50 } }] })).toBe(false)
    expect(isKnowledgeMap({ ...map, projection: { pending: 'yes' } })).toBe(false)
    expect(isKnowledgeDetail({ ...detail, evidence: [{ ...detail.evidence[0], result: 'great' }] })).toBe(false)
    expect(isAgentContext({ ...context, tips: [{ ...context.tips[0], kind: 'ranking' }] })).toBe(false)
    expect(isMemoryList({ student_id: 'student_one', items: [{ ...memory, category: 'personality' }], notice: 'n' })).toBe(false)
    expect(isOptionalActivities({ ...activities, gate: { open: 'true', reasons: [], remaining_minutes: 10 } })).toBe(false)
    expect(isVariantPracticeOverview({ ...variantOverview, groups: [{ ...variantOverview.groups[0], learning_status: 'mastered' }] })).toBe(false)
    expect(isOptionalActivities({ ...activities, gate: { open: true, reasons: [], remaining_minutes: -1 } })).toBe(false)
    expect(isPracticeSession({ ...session, status: 'failed' })).toBe(false)
    expect(isPracticeSession({ ...session, items: [{ ...session.items[0], answer_kind: 'essay' }] })).toBe(false)
    expect(isPracticeSession({ ...session, budget_ends_at: 'soon' })).toBe(false)
    for (const value of [null, [], 'x', 1]) expect(isKnowledgeMap(value)).toBe(false)
  })
})

class MemorySession implements Storage {
  private values = new Map<string, string>()
  get length() { return this.values.size }
  clear() { this.values.clear() }
  getItem(key: string) { return this.values.get(key) ?? null }
  key(index: number) { return [...this.values.keys()][index] ?? null }
  removeItem(key: string) { this.values.delete(key) }
  setItem(key: string, value: string) { this.values.set(key, value) }
}
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } })
const problem = (status: number, code: string, category = 'conflict') => json({ type: `https://errors.xueban.invalid/${code}`, title: code, status, category, code, detail: '合成', request_id: 'request_test', current_revision: null, retry_after_seconds: null }, status)

function harness(routes: Record<string, (init: RequestInit) => Response>) {
  const identity = { actor_id: 'actor_test', roles: ['student'], authorized_students: [{ student_id: 'student_one', display_name: '合成学生', campus_id: 'campus_one' }] }
  let me = () => json(identity)
  const calls: { path: string; init: RequestInit }[] = []
  const transport = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const path = new URL(String(input)).pathname; calls.push({ path, init })
    if (path === '/v1/auth/login') return json({ access_token: 'synthetic.access.token', expires_at: '2099-01-01T00:00:00Z', must_change_password: false })
    if (path === '/v1/auth/account') return json({ username: 'synthetic.student', student_id: 'student_one', must_change_password: false })
    if (path === '/v1/me') return me()
    const route = routes[`${init.method ?? 'GET'} ${path}`]; if (route) return route(init)
    throw new Error(`Unexpected synthetic route ${init.method} ${path}`)
  }) as unknown as typeof fetch
  const client = new StudentClient({ origin: 'http://localhost', storage: new MemorySession(), store: new DraftStore(new IDBFactory()), transport, normalize: async () => { throw new Error('unused') }, sleep: async () => undefined })
  return { client, calls, setMe: (next: () => Response) => { me = next }, login: () => client.login('synthetic.student', 'synthetic password only') }
}

describe('learning client', () => {
  it('reads the signed-in student map and refuses another student’s data', async () => {
    let body: unknown = map
    const s = harness({ 'GET /v1/students/student_one/knowledge': () => json(body) })
    await s.login()
    expect((await s.client.knowledgeMap()).states).toHaveLength(1)
    const auth = s.calls.find(c => c.path === '/v1/students/student_one/knowledge')!.init.headers as Record<string, string>
    expect(auth.Authorization).toBe('Bearer synthetic.access.token')
    body = { ...map, student_id: 'student_two' }
    await expect(s.client.knowledgeMap()).rejects.toMatchObject({ code: 'access_denied' })
    body = { ...map, states: [{ ...state, learning_status: 'mastered' }] }
    await expect(s.client.knowledgeMap()).rejects.toMatchObject({ code: 'invalid_response' })
  })
  it('keeps the voluntary-activity 409 codes the phase-one Problem enum does not list', async () => {
    let code = 'optional_gate_closed'
    const s = harness({ 'POST /v1/students/student_one/practice-sessions': () => problem(409, code) })
    await s.login()
    for (const next of ['optional_gate_closed', 'practice_not_needed', 'course_unknown', 'no_reviewed_items', 'practice_active']) {
      code = next
      await expect(s.client.startPractice({ purpose: 'variant', willing: true, knowledge_id: 'kp-rational-add-sub', count: 2 })).rejects.toMatchObject({ code: next, status: 409 })
    }
    const sent = s.calls.filter(c => c.path.endsWith('/practice-sessions')).at(-1)!.init
    expect(JSON.parse(String(sent.body))).toEqual({ purpose: 'variant', willing: true, knowledge_id: 'kp-rational-add-sub', count: 2 })
  })
  it('validates memory length locally and checks hint/answer item ids', async () => {
    const s = harness({
      'POST /v1/students/student_one/memories': init => json({ ...memory, content: JSON.parse(String(init.body)).content }, 201),
      'POST /v1/practice-sessions/practice_1/items/item_1/hint': () => json({ item_id: 'item_other', hint_text: '先看符号' }),
      'POST /v1/practice-sessions/practice_1/responses': () => problem(409, 'hints_disabled'),
    })
    await s.login()
    await expect(s.client.addMemory('learning_goal', ' 一 ')).rejects.toMatchObject({ code: 'memory_length' })
    await expect(s.client.addMemory('learning_goal', '好'.repeat(121))).rejects.toMatchObject({ code: 'memory_length' })
    expect((await s.client.addMemory('learning_goal', '  先自己检查一遍  ')).content).toBe('先自己检查一遍')
    await expect(s.client.practiceHint('practice_1', 'item_1')).rejects.toMatchObject({ code: 'invalid_response' })
    await expect(s.client.answerPractice('practice_1', 'item_1', '   ')).rejects.toMatchObject({ code: 'answer_length' })
  })
  it('signs out when the account becomes unavailable during a learning read', async () => {
    const s = harness({ 'GET /v1/students/student_one/memories': () => json({ student_id: 'student_one', items: [], notice: 'n' }) })
    await s.login()
    s.setMe(() => problem(403, 'access_denied', 'authorization'))
    const failure = await s.client.memories().catch((e: unknown) => e)
    expect(failure).toBeInstanceOf(StudentError); expect(failure).toMatchObject({ code: 'account_unavailable' })
    expect(s.client.identity).toBeNull(); expect(s.client.selectedStudentId).toBeNull()
    expect(s.calls.some(c => c.path === '/v1/students/student_one/memories')).toBe(false)
  })
  it('finds an unfinished activity to resume, ignoring ended or expired ones', async () => {
    const s = harness({ 'GET /v1/students/student_one/practice-sessions': () => json({ items: [{ ...session, id: 'practice_old', status: 'exited' }, { ...session, id: 'practice_late', budget_ends_at: '2020-01-01T00:00:00Z' }, session] }) })
    await s.login()
    expect((await s.client.activePractice())?.id).toBe('practice_1')
  })
})


it('validates optional model and staff evidence sources without treating missing legacy fields as confirmation', () => {
  expect(isKnowledgeMap({ ...map, states: [{ ...state, model_evidence_count: 2, staff_verified_evidence_count: 0, assessment_source: 'model' }] })).toBe(true)
  expect(isKnowledgeMap({ ...map, states: [{ ...state, assessment_source: 'verified_by_ai' }] })).toBe(false)
  expect(isKnowledgeDetail({ ...detail, evidence: detail.evidence.map(e => ({ ...e, source_role: 'model' })) })).toBe(true)
  expect(isKnowledgeDetail({ ...detail, evidence: detail.evidence.map(e => ({ ...e, source_role: 'guessed' })) })).toBe(false)
})
