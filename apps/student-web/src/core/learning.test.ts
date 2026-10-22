import { describe, expect, it, vi } from 'vitest'
import { IDBFactory } from 'fake-indexeddb'
import { StudentClient } from './student'
import { DraftStore } from './store'
import { StudentError } from './errors'
import { isCurriculumBooks, isStudentCurriculum, isAgentContext, isKnowledgeDetail, isKnowledgeMap, isMemoryList, isOptionalActivities, isPracticeAnswer, isPracticeAssessment, isPracticeSession, isVariantPracticeOverview, isVariantGeneration, isVariantGenerationList, type VariantGeneration, type PracticeAssessment } from './learning'
import { syntheticBook, syntheticBooks } from './knowledge.fixtures'
import { activities, context, detail, map, memory, session, state, variantOverview } from './learning.fixtures'

describe('learning response guards', () => {
  it('accept the backend shapes', () => {
    expect(isKnowledgeMap(map)).toBe(true); expect(isKnowledgeDetail(detail)).toBe(true); expect(isAgentContext(context)).toBe(true)
    expect(isMemoryList({ student_id: 'student_one', items: [memory], notice: 'n' })).toBe(true); expect(isOptionalActivities(activities)).toBe(true)
    expect(isVariantPracticeOverview(variantOverview)).toBe(true)
    expect(isVariantPracticeOverview({ ...variantOverview, groups: [{ ...variantOverview.groups[0], graph_version: null, curriculum_status: 'historical_unmatched' }] })).toBe(true)
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
    expect(isVariantPracticeOverview({ ...variantOverview, groups: [{ ...variantOverview.groups[0], curriculum_status: 'automatically_reassigned' }] })).toBe(false)
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
    for (const next of ['optional_gate_closed', 'practice_not_needed', 'course_unknown', 'no_reviewed_items', 'practice_active', 'curriculum_mismatch']) {
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

describe('explicit curriculum book selection', () => {
  it('validates book identity and never assumes a binding from the list of available books', () => {
    expect(isCurriculumBooks(syntheticBooks)).toBe(true)
    expect(isCurriculumBooks({ ...syntheticBooks, current_binding: syntheticBook })).toBe(true)
    expect(isCurriculumBooks({ ...syntheticBooks, current_binding: { graph_version: 'synthetic-pep-2026' } })).toBe(false)
    expect(isCurriculumBooks({ ...syntheticBooks, books: [{ ...syntheticBook, grade: 9 }] })).toBe(false)
    expect(isCurriculumBooks({ ...syntheticBooks, profile_revision: -1 })).toBe(false)
  })
  it('binds selection/clear to the observed profile revision and checks student ownership', async () => {
    let response = syntheticBooks
    const s = harness({
      'GET /v1/students/student_one/curriculum-books': () => json(response),
      'PUT /v1/students/student_one/curriculum-books': init => { const body = JSON.parse(String(init.body)); return json({ ...response, profile_revision: body.expected_revision + 1, current_binding: body.graph_version ? syntheticBook : null }) },
    })
    await s.login()
    expect((await s.client.curriculumBooks()).current_binding).toBeNull()
    expect((await s.client.selectCurriculumBook(syntheticBook.graph_version, 2)).current_binding?.isbn).toBe('9787107382468')
    const sent = s.calls.find(call => call.init.method === 'PUT')!
    expect(JSON.parse(String(sent.init.body))).toEqual({ expected_revision: 2, graph_version: syntheticBook.graph_version })
    expect((await s.client.selectCurriculumBook(null, 3)).current_binding).toBeNull()
    response = { ...syntheticBooks, student_id: 'another_student' }
    await expect(s.client.curriculumBooks()).rejects.toMatchObject({ code: 'access_denied' })
    await expect(s.client.selectCurriculumBook(syntheticBook.graph_version, -1)).rejects.toMatchObject({ code: 'invalid_curriculum_selection' })
  })
})

it('accepts the concrete scope returned by backend scope_payload in curriculum and knowledge responses', () => {
  // Exact non-personal shape from scope_payload(scope_for_profile(PEP ISBN binding)).
  const scope = { grade: 7, subject: '数学', publisher_code: 'PEP', publisher_name: '人民教育出版社', revision_policy: 'explicit_book',
    textbook_revision: 'isbn:9787107382468', stage: 'junior_middle', catalog_status: 'available', catalog_verified_at: '2026-10-21', catalog_candidates: [],
    isbn: '9787107382468', graph_version: 'test-pep-book-v1', books: [{ term: 'up', title: '义务教育教科书·数学七年级上册', isbn: '9787107382468', catalog_year: null,
      updated_at: null, revised_2022_standard: true, contents_review_status: 'source_verified_ai_reviewed' }] }
  const curriculum = { student_id: 'student_one', grade: 7, stage: 'junior_middle', subjects: [scope], notice: '具体教材' }
  expect(isStudentCurriculum(curriculum)).toBe(true)
  expect(isKnowledgeMap({ ...map, subjects: [{ ...scope, graph_ready: true, status: 'ready', correct_count: 0, incorrect_count: 0, partial_count: 0, scorable_count: 0, score_percent: null }] })).toBe(true)
  expect(isStudentCurriculum({ ...curriculum, subjects: [{ ...scope, textbook_revision: 'isbn:unknown' }] })).toBe(false)
  expect(isStudentCurriculum({ ...curriculum, subjects: [{ ...scope, revision_policy: 'teacher_reviewed' }] })).toBe(false)
})

const syntheticGeneration: VariantGeneration = { job_id: 'generation_one', student_id: 'student_one', source_case_id: 'case_one', graph_version: 'g1', knowledge_id: null, question_type: 'calculation', status: 'queued', item_id: null, failure_code: null, session_id: null, notice: '合成测试' }

describe('student-owned variant generation', () => {
  it('accepts real lifecycle states and rejects a ready task without an item', () => {
    for (const status of ['queued', 'generating', 'verifying', 'failed', 'superseded'] as const) expect(isVariantGeneration({ ...syntheticGeneration, status })).toBe(true)
    expect(isVariantGeneration({ ...syntheticGeneration, status: 'ready', item_id: 'item_one' })).toBe(true)
    expect(isVariantGeneration({ ...syntheticGeneration, status: 'ready' })).toBe(false)
    expect(isVariantGeneration({ ...syntheticGeneration, status: 'approved_by_teacher' })).toBe(false)
    expect(isVariantGenerationList({ student_id: 'student_one', items: [syntheticGeneration] })).toBe(true)
    expect(isPracticeSession({ ...session, knowledge_ids: [], items: [{ ...session.items[0], knowledge_id: null, question_type: 'calculation', question_kind: 'solution', verification: { source: 'ai', status: 'verified' } }] })).toBe(true)
    expect(isPracticeSession({ ...session, items: [{ ...session.items[0], verification: { source: 'ai', status: 'candidate' } }] })).toBe(false)
  })
  it('uses reads to restore progress and only explicit POST to request one source-bound question', async () => {
    const s = harness({
      'GET /v1/students/student_one/variant-generations': () => json({ student_id: 'student_one', items: [syntheticGeneration] }),
      'GET /v1/variant-generations/generation_one': () => json({ ...syntheticGeneration, status: 'verifying' }),
      'POST /v1/students/student_one/variant-generations': () => json(syntheticGeneration, 202),
      'POST /v1/students/student_one/practice-sessions': () => json({ ...session, items: [session.items[0]] }, 201),
    })
    await s.login()
    expect((await s.client.variantGenerations()).items).toHaveLength(1)
    expect((await s.client.variantGeneration('generation_one')).status).toBe('verifying')
    expect(s.calls.filter(call => call.path.includes('variant-generations')).every(call => call.init.method === 'GET')).toBe(true)
    await s.client.generateVariant({ willing: true, count: 1, source_case_id: 'case_one' })
    const create = s.calls.find(call => call.path.endsWith('/variant-generations') && call.init.method === 'POST')!
    expect(JSON.parse(String(create.init.body))).toEqual({ willing: true, count: 1, source_case_id: 'case_one' })
    expect((create.init.headers as Record<string, string>)['Idempotency-Key']).toBeTruthy()
    await s.client.startPractice({ willing: true, purpose: 'variant', generation_job_id: 'generation_one', count: 1 })
    const start = s.calls.find(call => call.path.endsWith('/practice-sessions') && call.init.method === 'POST')!
    expect(JSON.parse(String(start.init.body))).toEqual({ willing: true, purpose: 'variant', generation_job_id: 'generation_one', count: 1 })
  })
  it('refuses another student or another original question in generation responses', async () => {
    let body = syntheticGeneration
    const s = harness({
      'POST /v1/students/student_one/variant-generations': () => json(body, 202),
      'GET /v1/variant-generations/generation_one': () => json(body),
      'GET /v1/students/student_one/variant-generations': () => json({ student_id: 'student_one', items: [body] }),
    })
    await s.login()
    body = { ...syntheticGeneration, student_id: 'another_student' }
    await expect(s.client.variantGenerations()).rejects.toMatchObject({ code: 'access_denied' })
    await expect(s.client.variantGeneration('generation_one')).rejects.toMatchObject({ code: 'access_denied' })
    body = { ...syntheticGeneration, source_case_id: 'another_case' }
    await expect(s.client.generateVariant({ willing: true, count: 1, source_case_id: 'case_one' })).rejects.toMatchObject({ code: 'invalid_response' })
    body = { ...syntheticGeneration, job_id: 'another_job' }
    await expect(s.client.variantGeneration('generation_one')).rejects.toMatchObject({ code: 'invalid_response' })
  })
  it('submits separate blank ids and preserves retryable service failure without an incorrect result', async () => {
    const response = { item_id: 'item_1', result: 'unscorable', session_status: 'active', retryable: true, feedback: '本次暂未完成核验，请稍后重试。', solution_text: null }
    const s = harness({ 'POST /v1/practice-sessions/practice_1/responses': () => json(response, 201) })
    await s.login()
    const result = await s.client.answerPractice('practice_1', 'item_1', { answer_text: '第一空：a^2\n第二空：4a', answers: [{ field_id: 'area', answer_text: ' a^2 ' }, { field_id: 'perimeter', answer_text: ' 4a ' }] })
    expect(result).toEqual(response)
    const sent = s.calls.find(call => call.path.endsWith('/responses'))!
    expect(JSON.parse(String(sent.init.body)).answers).toEqual([{ field_id: 'area', answer_text: 'a^2' }, { field_id: 'perimeter', answer_text: '4a' }])
    await s.client.answerPractice('practice_1', 'item_1', 'x'.repeat(4000))
    await expect(s.client.answerPractice('practice_1', 'item_1', 'x'.repeat(4001))).rejects.toMatchObject({ code: 'answer_length' })
    await expect(s.client.answerPractice('practice_1', 'item_1', { answer_text: 'A,A', selected_options: ['A', 'A'] })).rejects.toMatchObject({ code: 'answer_length' })
  })
})

const syntheticAssessment: PracticeAssessment = { assessment_id: 'assessment_one', student_id: 'student_one', session_id: 'practice_1', item_id: 'item_1', status: 'queued', result: null, feedback: '正在核验本次作答。', solution_text: null, retryable: false, submission: { answer_text: '2(x+1)\n=2x+2', selected_options: null, answers: null } }

describe('asynchronous practice API boundaries', () => {
  it('accepts pending separately from correctness and rejects incomplete or contradictory statuses', () => {
    const receipt = { item_id: 'item_1', result: 'pending', assessment_id: 'assessment_one', session_status: 'active', feedback: '正在核验本次作答。', solution_text: null }
    expect(isPracticeAnswer(receipt)).toBe(true)
    expect(isPracticeAnswer({ ...receipt, assessment_id: undefined })).toBe(false)
    expect(isPracticeAnswer({ ...receipt, session_status: 'completed' })).toBe(false)
    expect(isPracticeAnswer({ ...receipt, solution_text: '参考答案泄漏' })).toBe(false)
    expect(isPracticeAssessment(syntheticAssessment)).toBe(true)
    expect(isPracticeAssessment({ ...syntheticAssessment, status: 'grading' })).toBe(true)
    expect(isPracticeAssessment({ ...syntheticAssessment, result: 'correct' })).toBe(false)
    expect(isPracticeAssessment({ ...syntheticAssessment, status: 'failed', result: 'unscorable', retryable: true })).toBe(true)
    expect(isPracticeAssessment({ ...syntheticAssessment, status: 'failed', result: 'correct', retryable: true })).toBe(false)
    expect(isPracticeAssessment({ ...syntheticAssessment, status: 'superseded', result: 'unscorable', retryable: true })).toBe(false)
    expect(isPracticeAssessment({ ...syntheticAssessment, status: 'completed', result: 'correct', solution_text: '2x+2' })).toBe(true)
  })
  it('binds restored assessments to the exact student/session/item rather than only validating field types', () => {
    const restored = { ...session, items: [{ ...session.items[0], assessment: syntheticAssessment }] }
    expect(isPracticeSession(restored)).toBe(true)
    for (const change of [{ student_id: 'other_student' }, { session_id: 'other_session' }, { item_id: 'other_item' }]) {
      expect(isPracticeSession({ ...restored, items: [{ ...restored.items[0], assessment: { ...syntheticAssessment, ...change } }] })).toBe(false)
    }
  })
  it('polls with GET only, checks identity, and accepts 202 as a receipt without retrying POST', async () => {
    let body = syntheticAssessment
    const s = harness({
      'POST /v1/practice-sessions/practice_1/responses': () => json({ item_id: 'item_1', result: 'pending', assessment_id: 'assessment_one', session_status: 'active', feedback: '正在核验本次作答。', solution_text: null }, 202),
      'GET /v1/practice-assessments/assessment_one': () => json(body),
      'GET /v1/practice-sessions/practice_1': () => json({ ...session, items: [{ ...session.items[0], assessment: body }] }),
    })
    await s.login()
    const result = await s.client.answerPractice('practice_1', 'item_1', '2(x+1)\n=2x+2')
    expect(result.result).toBe('pending')
    expect((await s.client.practiceAssessment('assessment_one', 'practice_1', 'item_1')).submission?.answer_text).toBe('2(x+1)\n=2x+2')
    expect((await s.client.practiceSession('practice_1')).items[0].assessment?.assessment_id).toBe('assessment_one')
    await s.client.practiceAssessment('assessment_one', 'practice_1', 'item_1')
    expect(s.calls.filter(call => call.path.endsWith('/responses') && call.init.method === 'POST')).toHaveLength(1)
    body = { ...syntheticAssessment, student_id: 'other_student' }
    await expect(s.client.practiceAssessment('assessment_one', 'practice_1', 'item_1')).rejects.toMatchObject({ code: 'access_denied' })
    body = { ...syntheticAssessment, session_id: 'other_session' }
    await expect(s.client.practiceAssessment('assessment_one', 'practice_1', 'item_1')).rejects.toMatchObject({ code: 'invalid_response' })
    body = { ...syntheticAssessment, assessment_id: 'other_assessment' }
    await expect(s.client.practiceAssessment('assessment_one', 'practice_1', 'item_1')).rejects.toMatchObject({ code: 'invalid_response' })
  })
  it('preserves assessment_pending conflicts for safe state recovery', async () => {
    const s = harness({ 'POST /v1/practice-sessions/practice_1/responses': () => problem(409, 'assessment_pending') })
    await s.login()
    await expect(s.client.answerPractice('practice_1', 'item_1', 'changed answer')).rejects.toMatchObject({ code: 'assessment_pending', status: 409 })
  })
})
