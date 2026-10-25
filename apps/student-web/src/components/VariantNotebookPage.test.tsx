import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { StudentError, type PracticeAssessment, type PracticeItem, type PracticeSession, type StudentClient, type VariantGeneration, type VariantNotebookDraft, type VariantNotebookQuestion } from '../core'
import type { NormalizedPhoto } from '../core/types'
import { normalizePhoto } from '../core/photo'
import { NotebookAnswerInput, NotebookCard, NotebookFeedback, NotebookModal, NotebookSolutionController, NotebookSolutionPanel, VariantNotebookController, VariantNotebookPage, notebookStatus, trapNotebookFocus, type NotebookClient } from './VariantNotebookPage'

vi.mock('../core/photo', async importOriginal => ({ ...await importOriginal<typeof import('../core/photo')>(), normalizePhoto: vi.fn() }))

const studentId = 'student_one'
const row = (overrides: Partial<VariantNotebookQuestion> = {}): VariantNotebookQuestion => ({
  case_id: 'case_one', question_id: 'question_one', question_text: '计算 2^3 + 1', homework_title: '周一数学作业',
  occurred_at: '2026-10-19T09:00:00Z', status: 'uncorrected', practiced: false, generation_id: null, session_id: null, ...overrides,
})
const item = (overrides: Partial<PracticeItem> = {}): PracticeItem => ({ id: 'item_one', knowledge_id: null, answer_kind: 'text',
  question_kind: 'solution', question_type: 'calculation', stem_text: '计算 3^2 + 1，写出过程。', has_hint: false, ...overrides })
const session = (overrides: Partial<PracticeSession> = {}): PracticeSession => ({
  id: 'session_one', student_id: studentId, purpose: 'variant', knowledge_ids: [], status: 'active', exit_reason: null,
  items: [item()], started_at: '2001-01-01T00:00:00Z', budget_ends_at: '2001-01-01T00:10:00Z', ended_at: null,
  hints_allowed: false, notice: '', no_expiry: true, ...overrides,
})
const job = (overrides: Partial<VariantGeneration> = {}): VariantGeneration => ({
  job_id: 'generation_one', student_id: studentId, source_case_id: 'case_one', graph_version: null, knowledge_id: null,
  question_type: 'calculation', status: 'queued', item_id: null, failure_code: null, notice: '', ...overrides,
})
const assessment = (overrides: Partial<PracticeAssessment> = {}): PracticeAssessment => ({
  assessment_id: 'assessment_one', student_id: studentId, session_id: 'session_one', item_id: 'item_one', status: 'grading',
  result: null, feedback: '正在核对', solution_text: null, retryable: false, submission: { answer_text: '9+1=10' }, ...overrides,
})
const photo: NormalizedPhoto = { blob: new Blob(['synthetic-photo'], { type: 'image/jpeg' }), width: 1200, height: 900, mime: 'image/jpeg', size: 15, sha256: 'a'.repeat(64) }
const draft = (overrides: Partial<VariantNotebookDraft> = {}): VariantNotebookDraft => ({
  generation_id: 'generation_one', session_id: 'session_one', regenerate_token: null,
  input: { text: '9+1=10', selected: [], fields: {} }, photo: null, ...overrides,
})
const clients = () => {
  const stored = new Map<string, VariantNotebookDraft>()
  return {
    selectedStudentId: studentId, historyScope: 'scope_one',
    variantNotebook: vi.fn<NotebookClient['variantNotebook']>(async () => ({ student_id: studentId, items: [row()] })),
    variantDraft: vi.fn<NotebookClient['variantDraft']>(async caseId => stored.get(caseId) ?? null),
    saveVariantDraft: vi.fn<NotebookClient['saveVariantDraft']>(async (caseId, value) => { if (value) stored.set(caseId, structuredClone(value)); else stored.delete(caseId) }),
    generateVariant: vi.fn<NotebookClient['generateVariant']>(async () => job()),
    variantGeneration: vi.fn<NotebookClient['variantGeneration']>(async () => job({ status: 'ready', item_id: 'item_one' })),
    startPractice: vi.fn<NotebookClient['startPractice']>(async () => session()),
    practiceSession: vi.fn<NotebookClient['practiceSession']>(async () => session()),
    practiceAssessment: vi.fn<NotebookClient['practiceAssessment']>(async () => assessment()),
    practiceSolution: vi.fn<NotebookClient['practiceSolution']>(async () => ({ item_id: 'item_one', answer_text: '10', solution_text: '先算 3^2 = 9，再算 9 + 1 = 10。' })),
    retryPractice: vi.fn<NotebookClient['retryPractice']>(async () => session({ id: 'session_retry' })),
    answerPractice: vi.fn<NotebookClient['answerPractice']>(async () => ({ item_id: 'item_one', result: 'pending', assessment_id: 'assessment_one', solution_text: null, session_status: 'active', feedback: '正在核对' })),
    answerPracticePhoto: vi.fn<NotebookClient['answerPracticePhoto']>(async () => ({ item_id: 'item_one', result: 'pending', assessment_id: 'assessment_one', solution_text: null, session_status: 'active', feedback: '正在核对照片' })),
    subscribe: vi.fn(() => () => {}), objectUrl: vi.fn(() => 'blob:synthetic'), releaseObjectUrl: vi.fn(),
  } satisfies NotebookClient & Pick<StudentClient, 'subscribe' | 'objectUrl' | 'releaseObjectUrl'>
}
type ClientMock = ReturnType<typeof clients>
const controllers: VariantNotebookController[] = []
const solutions: NotebookSolutionController[] = []
const setup = async (client: ClientMock = clients()) => {
  const controller = new VariantNotebookController(client, studentId); controllers.push(controller); controller.start(); await controller.refresh()
  return { controller, client }
}
const openSession = async (client = clients(), original = row({ session_id: 'session_one', generation_id: 'generation_one' })) => {
  const context = await setup(client); await context.controller.open(original); return context
}
const flush = async () => { for (let i = 0; i < 20; i++) await Promise.resolve() }
const deferred = <T,>() => { let resolve!: (value: T) => void; const promise = new Promise<T>(done => { resolve = done }); return { promise, resolve } }
const activeEntry = (controller: VariantNotebookController) => controller.getSnapshot().entries.case_one
const solutionFor = (controller: VariantNotebookController) => {
  const current = activeEntry(controller).session!
  const solution = new NotebookSolutionController(controller, 'case_one', current.id, current.items[0].id)
  solutions.push(solution); solution.start(); return solution
}
beforeEach(() => { vi.useFakeTimers(); vi.mocked(normalizePhoto).mockResolvedValue(photo) })
afterEach(() => { solutions.splice(0).forEach(solution => solution.dispose()); controllers.splice(0).forEach(controller => controller.dispose()); vi.useRealTimers(); vi.clearAllMocks() })

describe('notebook generation and persistence', () => {
  it.each(['rechecking', 'awaiting_review'] as const)('shows original %s separately and keeps an existing practice accessible', original_status => {
    const value = row({ original_status, session_id: 'session_one', generation_id: 'generation_one', status: 'correct', practiced: true })
    const html = renderToStaticMarkup(<NotebookCard item={value} onOpen={() => {}} />)
    expect(html).toContain(original_status === 'rechecking' ? '原题正在重新检查' : '原题待老师核实')
    expect(html).toContain('已有练习记录会保留')
    expect(html).not.toContain('disabled=')
    expect(html).toContain('已做对')
  })
  it('waits for the original review before offering a new practice', () => {
    const html = renderToStaticMarkup(<NotebookCard item={row({ original_status: 'awaiting_review' })} onOpen={() => {}} />)
    expect(html).toContain('disabled=')
    expect(html).toContain('原题待老师核实')
  })
  it('shows generation immediately after regenerate and exit, through polling, then stops on completion', async () => {
    const { controller, client } = await openSession(), pending = deferred<VariantGeneration>()
    client.generateVariant.mockReturnValueOnce(pending.promise)
    const regenerating = controller.regenerate('case_one'); controller.close()
    const renderCard = () => renderToStaticMarkup(<NotebookCard item={row({ generation_id: 'generation_one', generation_status: 'ready' })} entry={activeEntry(controller)} onOpen={() => {}} />)
    expect(renderCard()).toContain('生成中'); expect(renderCard()).toContain('spin'); expect(renderCard()).not.toContain('未纠错')
    await flush(); pending.resolve(job({ job_id: 'generation_new' })); await regenerating
    expect(controller.getSnapshot().activeCase).toBeNull(); expect(renderCard()).toContain('生成中')
    await controller.open(row()); expect(client.generateVariant).toHaveBeenCalledTimes(1)
    controller.close(); client.variantGeneration.mockResolvedValue(job({ job_id: 'generation_new', status: 'ready', item_id: 'item_one' }))
    await controller.poll()
    expect(renderCard()).not.toContain('spin'); expect(renderCard()).toContain('未纠错')
  })
  it.each(['queued', 'generating', 'verifying'] as const)('shows server %s progress without reopening a dialog after reload', status => {
    const html = renderToStaticMarkup(<NotebookCard item={row({ generation_id: 'generation_one', generation_status: status })} onOpen={() => {}} />)
    expect(html).toContain('生成中'); expect(html).toContain('role="status"'); expect(html).toContain('spin')
    expect(html).not.toContain('disabled='); expect(html).toContain('举一反三练习')
  })
  it('reconciles terminal/newer server jobs with local polling without stale spinners', async () => {
    const { controller } = await setup(); await controller.open(row())
    const renderCard = (value: VariantNotebookQuestion) => renderToStaticMarkup(<NotebookCard item={value} entry={activeEntry(controller)} onOpen={() => {}} />)
    for (const status of ['ready', 'failed', 'superseded'] as const) {
      expect(renderCard(row({ generation_id: 'generation_one', generation_status: status }))).not.toContain('spin')
    }
    expect(renderCard(row({ generation_id: 'generation_new', generation_status: 'failed' }))).toContain('生成未完成')
    await controller.poll()
    expect(renderCard(row({ generation_id: 'generation_one', generation_status: 'queued' }))).not.toContain('spin')
    expect(renderCard(row({ generation_id: 'generation_new', generation_status: 'queued' }))).toContain('生成中')
  })
  it('stops the indicator on failed generation or failed request, without marking the answer wrong', async () => {
    const { controller, client } = await setup(); await controller.open(row()); controller.close()
    client.variantGeneration.mockResolvedValue(job({ status: 'failed' })); await controller.poll()
    const renderCard = () => renderToStaticMarkup(<NotebookCard item={row()} entry={activeEntry(controller)} onOpen={() => {}} />)
    expect(renderCard()).toContain('生成未完成'); expect(renderCard()).not.toContain('spin')
    client.generateVariant.mockRejectedValue(new StudentError('network', 0))
    await controller.regenerate('case_one')
    expect(activeEntry(controller).generationRequest).toBe(false); expect(renderCard()).not.toContain('spin')
    expect(controller.getSnapshot().items[0].status).toBe('uncorrected')
  })
  it('opens immediately, generates only after the explicit open, and starts the verified item while the dialog is closed', async () => {
    const { controller, client } = await setup()
    const reading = deferred<VariantNotebookDraft | null>(); client.variantDraft.mockReturnValueOnce(reading.promise)
    expect(client.generateVariant).not.toHaveBeenCalled()
    const opening = controller.open(row())
    expect(controller.getSnapshot().activeCase).toBe('case_one'); expect(activeEntry(controller).busy).toBe(true)
    controller.close(); reading.resolve(null); await opening
    expect(client.generateVariant).toHaveBeenCalledExactlyOnceWith({ willing: true, count: 1, source_case_id: 'case_one' })
    await controller.poll()
    expect(client.startPractice).toHaveBeenCalledExactlyOnceWith({ purpose: 'variant', willing: true, count: 1, generation_job_id: 'generation_one' })
    expect(controller.getSnapshot().activeCase).toBeNull(); expect(activeEntry(controller).session?.id).toBe('session_one')
    await controller.open(row()); expect(client.generateVariant).toHaveBeenCalledTimes(1); expect(client.startPractice).toHaveBeenCalledTimes(1)
  })
  it('resumes stored sessions, text and photo drafts without a generation or start request', async () => {
    const client = clients(); client.variantDraft.mockResolvedValue(draft({ photo }))
    const { controller } = await openSession(client)
    expect(activeEntry(controller).input.text).toBe('9+1=10'); expect(activeEntry(controller).photo).toEqual(photo)
    expect(client.practiceSession).toHaveBeenCalledWith('session_one'); expect(client.generateVariant).not.toHaveBeenCalled(); expect(client.startPractice).not.toHaveBeenCalled()
    controller.close(); await controller.open(row())
    expect(activeEntry(controller).photo).toEqual(photo)
  })
  it('restores each partial blank by identity and saves ordered typing without flattening the draft', async () => {
    const client = clients(); client.practiceSession.mockResolvedValue(session({ items: [item({ question_kind: 'fill_blank', answer_fields: [{ id: 'area', label: '面积' }, { id: 'length', label: '边长' }] })] }))
    client.variantDraft.mockResolvedValue(draft({ input: { text: '', selected: [], fields: { area: 'x^2', length: '' } } }))
    const { controller } = await openSession(client)
    expect(activeEntry(controller).input.fields).toEqual({ area: 'x^2', length: '' })
    controller.changeInput('case_one', { text: '', selected: [], fields: { area: 'x^2', length: 'x' } }); await flush()
    expect(client.saveVariantDraft.mock.lastCall?.[1]?.input.fields).toEqual({ area: 'x^2', length: 'x' })
    await controller.submit('case_one')
    expect(client.answerPractice).toHaveBeenCalledWith('session_one', 'item_one', { answer_text: '面积：x^2\n边长：x', answers: [{ field_id: 'area', answer_text: 'x^2' }, { field_id: 'length', answer_text: 'x' }] })
  })
  it('reuses one regenerate token after a lost receipt and after reopening the saved draft', async () => {
    const { controller, client } = await openSession()
    client.generateVariant.mockRejectedValueOnce(new StudentError('network', 0))
    await controller.regenerate('case_one')
    const token = client.generateVariant.mock.calls[0][0].regenerate_token
    expect(token).toMatch(/^[a-f0-9-]{36}$/); expect(activeEntry(controller).regenerateToken).toBe(token)
    const saved = client.saveVariantDraft.mock.lastCall?.[1]
    expect(saved?.regenerate_token).toBe(token); expect(saved?.session_id).toBeNull()
    controller.dispose()
    const next = await setup(client); await next.controller.open(row({ session_id: 'old_session', generation_id: 'old_generation' }))
    expect(client.generateVariant.mock.calls.at(-1)?.[0].regenerate_token).toBe(token)
    expect(client.practiceSession).not.toHaveBeenCalledWith('old_session')
  })
  it('normalizes and persists a camera/album photo, then sends it through the photo endpoint', async () => {
    const { controller, client } = await openSession()
    const original = new File(['raw'], 'answer.png', { type: 'image/png' })
    await controller.choosePhoto('case_one', original)
    expect(normalizePhoto).toHaveBeenCalledWith(original)
    expect(client.saveVariantDraft.mock.lastCall?.[1]?.photo).toEqual(photo)
    await controller.submit('case_one')
    expect(client.answerPracticePhoto).toHaveBeenCalledExactlyOnceWith('session_one', 'item_one', photo)
    expect(client.answerPractice).not.toHaveBeenCalled(); expect(activeEntry(controller).session?.items[0].assessment?.status).toBe('queued')
  })
  it('does not erase a kept photo when replacement processing fails', async () => {
    const client = clients(); client.variantDraft.mockResolvedValue(draft({ photo }))
    const { controller } = await openSession(client)
    vi.mocked(normalizePhoto).mockRejectedValueOnce(new StudentError('invalid_photo', 422, '照片无法打开'))
    await controller.choosePhoto('case_one', new File(['bad'], 'bad.jpg'))
    expect(activeEntry(controller).photo).toEqual(photo); expect(activeEntry(controller).error).toBe('照片无法打开')
  })
  it('does not use a previous generated question’s input or photo for a newer generation', async () => {
    const client = clients(); client.variantDraft.mockResolvedValue(draft({ generation_id: 'old_generation', photo }))
    const { controller } = await openSession(client)
    expect(activeEntry(controller).input.text).toBe(''); expect(activeEntry(controller).photo).toBeNull()
  })
  it('re-reads the latest session when reopening and follows a newer server generation', async () => {
    const { controller, client } = await openSession()
    controller.close()
    client.practiceSession.mockResolvedValue(session({ id: 'session_new', items: [item({ id: 'item_new', stem_text: '另一道题' })] }))
    await controller.open(row({ session_id: 'session_new', generation_id: 'generation_new' }))
    expect(client.practiceSession).toHaveBeenLastCalledWith('session_new')
    expect(activeEntry(controller).session?.items[0].stem_text).toBe('另一道题')
    expect(client.generateVariant).not.toHaveBeenCalled()
  })
  it('does not resume an old saved session when the server has a newer unstarted generation', async () => {
    const client = clients(); client.variantDraft.mockResolvedValue(draft({ generation_id: 'old_generation', session_id: 'old_session', photo }))
    const { controller } = await setup(client)
    await controller.open(row({ generation_id: 'generation_one', session_id: null }))
    expect(client.practiceSession).not.toHaveBeenCalledWith('old_session')
    expect(client.variantGeneration).toHaveBeenCalledWith('generation_one'); expect(client.startPractice).toHaveBeenCalledTimes(1)
    expect(activeEntry(controller).input.text).toBe(''); expect(activeEntry(controller).photo).toBeNull()
  })
})

describe('real answer states and background refresh', () => {
  it('polls an assessment after exit and refreshes card status from the notebook endpoint', async () => {
    const { controller, client } = await openSession()
    controller.changeInput('case_one', { text: '9+1=10', selected: [], fields: {} }); await controller.submit('case_one')
    expect(activeEntry(controller).session?.items[0].response).toBeUndefined()
    expect(controller.getSnapshot().items[0].status).toBe('uncorrected')
    controller.close()
    client.practiceAssessment.mockResolvedValue(assessment({ status: 'completed', result: 'correct' }))
    client.practiceSession.mockResolvedValue(session({ status: 'completed', items: [item({ response: { answer_text: '9+1=10', result: 'correct', hint_used: false } })] }))
    client.variantNotebook.mockResolvedValue({ student_id: studentId, items: [row({ status: 'correct', practiced: true })] })
    await controller.poll(); await flush()
    expect(client.practiceAssessment).toHaveBeenCalledWith('assessment_one', 'session_one', 'item_one')
    expect(controller.getSnapshot().items[0].status).toBe('correct'); expect(controller.getSnapshot().activeCase).toBeNull()
  })
  it('refreshes unopened notebook cards every six seconds, including newly completed homework', async () => {
    const { controller, client } = await setup()
    client.variantNotebook.mockResolvedValue({ student_id: studentId, items: [row(), row({ case_id: 'case_two', question_text: '后台完成的新错题', status: 'submitted' })] })
    await vi.advanceTimersByTimeAsync(6000)
    expect(client.variantNotebook).toHaveBeenCalledTimes(2); expect(controller.getSnapshot().items).toHaveLength(2)
    expect(client.generateVariant).not.toHaveBeenCalled(); expect(client.practiceSession).not.toHaveBeenCalled()
    controller.dispose(); await vi.advanceTimersByTimeAsync(12000)
    expect(client.variantNotebook).toHaveBeenCalledTimes(2)
  })
  it('coalesces manual and scheduled list refreshes while a slow request is in flight', async () => {
    const client = clients(), slow = deferred<Awaited<ReturnType<NotebookClient['variantNotebook']>>>()
    client.variantNotebook.mockReturnValueOnce(slow.promise)
    const controller = new VariantNotebookController(client, studentId); controllers.push(controller); controller.start()
    void controller.refresh(); void controller.refresh(); await vi.advanceTimersByTimeAsync(12000)
    expect(client.variantNotebook).toHaveBeenCalledTimes(1)
    slow.resolve({ student_id: studentId, items: [row()] }); await flush()
    expect(controller.getSnapshot().loading).toBe(false)
  })
  it('retries wrong answers in the server-created retry session and preserves the original response', async () => {
    const wrong = session({ status: 'completed', items: [item({ response: { answer_text: '8', result: 'incorrect', hint_used: false }, solution_text: '9+1=10' })] })
    const client = clients(); client.practiceSession.mockResolvedValue(wrong)
    const { controller } = await openSession(client)
    await controller.retry('case_one')
    expect(client.retryPractice).toHaveBeenCalledExactlyOnceWith('session_one')
    expect(wrong.items[0].response?.answer_text).toBe('8'); expect(wrong.items[0].response?.result).toBe('incorrect')
    controller.changeInput('case_one', { text: '10', selected: [], fields: {} }); await controller.submit('case_one')
    expect(client.answerPractice).toHaveBeenCalledWith('session_retry', 'item_one', { answer_text: '10' })
    expect(client.generateVariant).not.toHaveBeenCalled()
  })
  it('shows assessment failures as unjudged and allows retrying the preserved answer without creating a wrong result', async () => {
    const failed = assessment({ status: 'failed', result: 'unscorable', feedback: '服务暂不可用', retryable: true })
    const client = clients(); client.practiceSession.mockResolvedValue(session({ items: [item({ assessment: failed })] }))
    const { controller } = await openSession(client)
    expect(activeEntry(controller).input.text).toBe('9+1=10')
    const html = renderToStaticMarkup(<NotebookFeedback item={item({ assessment: failed })} />)
    expect(html).toContain('尚未判定对错'); expect(html).not.toContain('还需要再想一想')
    await controller.submit('case_one'); expect(client.answerPractice).toHaveBeenCalledTimes(1); expect(client.retryPractice).not.toHaveBeenCalled()
    expect(activeEntry(controller).session?.items[0].response).toBeUndefined()
  })
  it('locks an unconfirmed answer, reads server state, and never posts a duplicate during assessment', async () => {
    const { controller, client } = await openSession()
    controller.changeInput('case_one', { text: '10', selected: [], fields: {} })
    client.answerPractice.mockRejectedValueOnce(new StudentError('network', 0))
    client.practiceSession.mockRejectedValueOnce(new StudentError('network', 0))
    await controller.submit('case_one'); expect(activeEntry(controller).unconfirmed).toBe(true)
    await controller.submit('case_one'); expect(client.answerPractice).toHaveBeenCalledTimes(1)
    client.practiceSession.mockResolvedValue(session({ items: [item({ assessment: assessment() })] }))
    await controller.poll(); expect(activeEntry(controller).unconfirmed).toBe(false)
    await controller.submit('case_one'); expect(client.answerPractice).toHaveBeenCalledTimes(1)
  })
  it('leaves generation progress retryable when a poll fails without adding another generation', async () => {
    const { controller, client } = await setup(); await controller.open(row())
    client.variantGeneration.mockRejectedValueOnce(new StudentError('network', 0))
    await controller.poll(); expect(activeEntry(controller).error).not.toBe('')
    await controller.poll(); expect(activeEntry(controller).session?.id).toBe('session_one')
    expect(client.generateVariant).toHaveBeenCalledTimes(1)
  })
  it('does not locally expire an old generated session or disable its answer', async () => {
    const { controller, client } = await openSession()
    controller.changeInput('case_one', { text: '10', selected: [], fields: {} }); await controller.submit('case_one')
    expect(client.answerPractice).toHaveBeenCalledTimes(1)
  })
})

describe('student and request fences', () => {
  it('discards generation completion after a student switch and never starts or saves into the new student', async () => {
    const { controller, client } = await setup(), pending = deferred<VariantGeneration>()
    client.generateVariant.mockReturnValueOnce(pending.promise)
    const opening = controller.open(row()); await flush()
    client.selectedStudentId = 'student_two'; client.historyScope = 'scope_two'
    pending.resolve(job({ status: 'ready', item_id: 'item_one' })); await opening
    expect(client.startPractice).not.toHaveBeenCalled(); expect(client.saveVariantDraft).not.toHaveBeenCalled()
  })
  it('rejects foreign notebook and session responses without displaying private text', async () => {
    const client = clients(); client.variantNotebook.mockResolvedValue({ student_id: 'student_two', items: [row({ question_text: 'private student two' })] })
    const { controller } = await setup(client)
    expect(controller.getSnapshot().items).toEqual([])
    client.practiceSession.mockResolvedValue(session({ student_id: 'student_two', items: [item({ stem_text: 'private student two' })] }))
    await controller.open(row({ session_id: 'session_one' }))
    expect(activeEntry(controller).session).toBeNull(); expect(activeEntry(controller).error).not.toBe('')
  })
  it('renders a student-switch barrier instead of the previous student’s page', () => {
    const client = clients(); client.selectedStudentId = 'student_two'
    const html = renderToStaticMarkup(<VariantNotebookPage client={client as unknown as StudentClient} studentId={studentId} initialCaseId="case_one" />)
    expect(html).toContain('正在切换学生'); expect(html).not.toContain('notebook-grid'); expect(client.generateVariant).not.toHaveBeenCalled()
  })
  it('ignores assessment results after the notebook has unmounted', async () => {
    const client = clients(); client.practiceSession.mockResolvedValue(session({ items: [item({ assessment: assessment() })] }))
    const { controller } = await openSession(client), pending = deferred<PracticeAssessment>()
    client.practiceAssessment.mockReturnValueOnce(pending.promise)
    const polling = controller.poll(); controller.dispose()
    pending.resolve(assessment({ status: 'completed', result: 'correct' })); await polling
    expect(activeEntry(controller).session?.items[0].assessment?.status).toBe('grading')
  })
})

describe('requested answers and steps', () => {
  it('offers a collapsed answer before submission without fetching or rendering existing solution text', async () => {
    const client = clients(); client.practiceSession.mockResolvedValue(session({ items: [item({ solution_text: '不应自动展示的解析' })] }))
    const { controller } = await openSession(client)
    const html = renderToStaticMarkup(<NotebookModal entry={activeEntry(controller)} controller={controller} client={client as unknown as StudentClient} />)
    expect(html).toContain('查看答案与步骤'); expect(html).toContain('aria-expanded="false"'); expect(html).toContain('aria-controls=')
    expect(html).toContain('提交答案'); expect(html).not.toContain('不应自动展示的解析')
    expect(client.practiceSolution).not.toHaveBeenCalled()
  })
  it('deduplicates clicks, keeps a closed response hidden, then reuses its answer and MathText steps', async () => {
    const { controller, client } = await openSession(), solution = solutionFor(controller)
    const pending = deferred<Awaited<ReturnType<NotebookClient['practiceSolution']>>>()
    client.practiceSolution.mockReturnValueOnce(pending.promise)
    const opening = solution.open(); void solution.open(); void solution.retry()
    expect(client.practiceSolution).toHaveBeenCalledExactlyOnceWith('session_one', 'item_one')
    expect(renderToStaticMarkup(<NotebookSolutionPanel controller={solution} />)).toContain('正在加载答案')
    solution.close(); void solution.open(); solution.close()
    pending.resolve({ item_id: 'item_one', answer_text: 'x^2', solution_text: '先算 3^2 = 9，再算 9 + 1 = 10。' }); await opening
    expect(solution.getSnapshot()).toMatchObject({ expanded: false, loading: false, error: '' })
    expect(renderToStaticMarkup(<NotebookSolutionPanel controller={solution} />)).not.toContain('正确答案')
    await solution.open()
    const html = renderToStaticMarkup(<NotebookSolutionPanel controller={solution} />)
    expect(html).toContain('正确答案'); expect(html).toContain('解题步骤'); expect(html.match(/role="math"/g)).toHaveLength(2)
    expect(client.practiceSolution).toHaveBeenCalledTimes(1)
    expect(controller.getSnapshot().items[0]).toMatchObject({ practiced: false, status: 'uncorrected' })
    expect(activeEntry(controller).session?.items[0].response).toBeUndefined()
    expect(client.answerPractice).not.toHaveBeenCalled(); expect(client.generateVariant).not.toHaveBeenCalled()
  })
  it('shows a concise error and only retries on request, with one retry in flight', async () => {
    const { controller, client } = await openSession(), solution = solutionFor(controller)
    client.practiceSolution.mockRejectedValueOnce(new Error('internal diagnostic'))
    await solution.open()
    const html = renderToStaticMarkup(<NotebookSolutionPanel controller={solution} />)
    expect(html).toContain('role="alert"'); expect(html).toContain('答案暂时没加载出来，请重试。'); expect(html).toContain('>重试</button>')
    expect(html).not.toContain('internal diagnostic')
    solution.close(); await solution.open(); expect(client.practiceSolution).toHaveBeenCalledTimes(1)
    const pending = deferred<Awaited<ReturnType<NotebookClient['practiceSolution']>>>()
    client.practiceSolution.mockReturnValueOnce(pending.promise)
    const retrying = solution.retry(); void solution.retry()
    expect(solution.getSnapshot()).toMatchObject({ loading: true, error: '' }); expect(client.practiceSolution).toHaveBeenCalledTimes(2)
    pending.resolve({ item_id: 'item_one', answer_text: '10', solution_text: '9 + 1 = 10' }); await retrying
    expect(solution.getSnapshot()).toMatchObject({ expanded: true, loading: false, solution: { answer_text: '10' } })
  })
  it.each(['queued', 'grading', 'completed'] as const)('blocks the first reveal while %s awaits its final response', async status => {
    const client = clients(); client.practiceSession.mockResolvedValue(session({ items: [item({ assessment: assessment({ status }) })] }))
    const { controller } = await openSession(client), solution = solutionFor(controller)
    await solution.open(); await solution.retry()
    expect(client.practiceSolution).not.toHaveBeenCalled(); expect(solution.getSnapshot().expanded).toBe(false)
    const html = renderToStaticMarkup(<NotebookModal entry={activeEntry(controller)} controller={controller} client={client as unknown as StudentClient} />)
    expect(html).toContain('批改完成后可查看答案'); expect(html).toMatch(/notebook-solution-toggle[^>]*disabled=""/)
    client.practiceSession.mockResolvedValue(session({ items: [item({ response: { answer_text: '10', result: 'correct', hint_used: false } })] }))
    await controller.prepare('case_one'); await solution.open()
    expect(client.practiceSolution).toHaveBeenCalledTimes(1); expect(solution.getSnapshot().expanded).toBe(true)
  })
  it('keeps a previously loaded answer usable and stable during submission and assessment polls', async () => {
    const { controller, client } = await openSession(), solution = solutionFor(controller)
    await solution.open()
    controller.changeInput('case_one', { text: '10', selected: [], fields: {} }); await controller.submit('case_one')
    const previous = solution.getSnapshot(), revision = activeEntry(controller).solutionRevision
    await controller.poll()
    expect(solution.getSnapshot()).toBe(previous); expect(activeEntry(controller).solutionRevision).toBe(revision)
    solution.close(); await solution.open()
    const html = renderToStaticMarkup(<NotebookSolutionPanel controller={solution} waiting />)
    expect(html).toContain('查看答案与步骤'); expect(html).not.toContain('批改完成后可查看答案'); expect(html).not.toContain('disabled=""')
    expect(client.practiceSolution).toHaveBeenCalledTimes(1)
  })
  it('blocks retrying a failed reveal during grading without clearing its recoverable error', async () => {
    const { controller, client } = await openSession(), solution = solutionFor(controller)
    client.practiceSolution.mockRejectedValueOnce(new StudentError('network', 0)); await solution.open()
    controller.changeInput('case_one', { text: '10', selected: [], fields: {} }); await controller.submit('case_one')
    await solution.retry(); expect(client.practiceSolution).toHaveBeenCalledTimes(1)
    const html = renderToStaticMarkup(<NotebookSolutionPanel controller={solution} waiting />)
    expect(html).toContain('答案暂时没加载出来'); expect(html).toMatch(/disabled=""[^>]*>重试</)
    solution.close(); await solution.open(); expect(solution.getSnapshot().expanded).toBe(false)
  })
  it('reopens collapsed and ignores an old request even when the dialog returns to the same session', async () => {
    const { controller, client } = await openSession(), solution = solutionFor(controller)
    const pending = deferred<Awaited<ReturnType<NotebookClient['practiceSolution']>>>()
    client.practiceSolution.mockReturnValueOnce(pending.promise)
    const opening = solution.open(); controller.close(); solution.dispose()
    await controller.open(row({ session_id: 'session_one', generation_id: 'generation_one' }))
    const reopened = solutionFor(controller)
    pending.resolve({ item_id: 'item_one', answer_text: '旧请求答案', solution_text: '旧请求步骤' }); await opening
    expect(solution.getSnapshot()).toMatchObject({ expanded: false, solution: null })
    expect(reopened.getSnapshot()).toMatchObject({ expanded: false, solution: null })
    await reopened.open(); expect(reopened.getSnapshot().solution?.answer_text).toBe('10')
  })
  it.each(['retry', 'regenerate'] as const)('discards an old session answer after %s, even with the same item id', async action => {
    const { controller, client } = await openSession(), solution = solutionFor(controller)
    const pending = deferred<Awaited<ReturnType<NotebookClient['practiceSolution']>>>()
    client.practiceSolution.mockReturnValueOnce(pending.promise)
    const opening = solution.open()
    if (action === 'regenerate') {
      client.generateVariant.mockResolvedValueOnce(job({ job_id: 'generation_new', status: 'ready', item_id: 'item_one' }))
      client.startPractice.mockResolvedValueOnce(session({ id: 'session_new' }))
    }
    await controller[action]('case_one')
    const fresh = solutionFor(controller)
    pending.resolve({ item_id: 'item_one', answer_text: '旧会话答案', solution_text: '旧会话步骤' }); await opening
    expect(solution.getSnapshot().solution).toBeNull(); expect(fresh.getSnapshot()).toMatchObject({ expanded: false, solution: null })
    await fresh.open()
    expect(client.practiceSolution).toHaveBeenLastCalledWith(action === 'retry' ? 'session_retry' : 'session_new', 'item_one')
  })
  it('resets the disclosure on a failed retry and on retrying grading in the same session', async () => {
    const { controller, client } = await openSession()
    client.retryPractice.mockRejectedValueOnce(new StudentError('network', 0))
    await controller.retry('case_one'); expect(activeEntry(controller).solutionRevision).toBe(1)
    client.practiceSession.mockResolvedValueOnce(session({ items: [item({ assessment: assessment({ status: 'failed', retryable: true }) })] }))
    await controller.prepare('case_one'); await controller.submit('case_one')
    expect(activeEntry(controller).session?.id).toBe('session_one'); expect(activeEntry(controller).solutionRevision).toBe(2)
  })
  it.each(['student', 'scope', 'dispose'] as const)('fences late solution responses after %s changes', async change => {
    const { controller, client } = await openSession(), solution = solutionFor(controller)
    const pending = deferred<Awaited<ReturnType<NotebookClient['practiceSolution']>>>()
    client.practiceSolution.mockReturnValueOnce(pending.promise)
    const opening = solution.open(), listener = vi.fn(); solution.subscribe(listener)
    if (change === 'student') client.selectedStudentId = 'student_two'
    else if (change === 'scope') client.historyScope = 'scope_two'
    else controller.dispose()
    pending.resolve({ item_id: 'item_one', answer_text: '旧学生答案', solution_text: '旧学生步骤' }); await opening
    expect(solution.getSnapshot().solution).toBeNull(); expect(listener).not.toHaveBeenCalled()
    await solution.open(); expect(client.practiceSolution).toHaveBeenCalledTimes(1)
  })
  it('rejects a mismatched solution item without exposing its contents', async () => {
    const { controller, client } = await openSession(), solution = solutionFor(controller)
    client.practiceSolution.mockResolvedValueOnce({ item_id: 'other_item', answer_text: '别题答案', solution_text: '别题步骤' })
    await solution.open()
    expect(solution.getSnapshot().solution).toBeNull(); expect(solution.getSnapshot().error).not.toBe('')
  })
  it('preserves assistance for an immediate local answer across closing and reopening', async () => {
    const { controller, client } = await openSession(), solution = solutionFor(controller)
    await solution.open(); controller.close(); solution.dispose()
    await controller.open(row({ session_id: 'session_one', generation_id: 'generation_one' }))
    client.answerPractice.mockResolvedValueOnce({ item_id: 'item_one', result: 'correct', feedback: '计算正确', solution_text: '9 + 1 = 10', session_status: 'completed' })
    client.practiceSession.mockRejectedValueOnce(new StudentError('network', 0))
    controller.changeInput('case_one', { text: '10', selected: [], fields: {} }); await controller.submit('case_one')
    expect(activeEntry(controller).session?.items[0].response).toMatchObject({ result: 'correct', hint_used: true })
    expect(activeEntry(controller).unconfirmed).toBe(false)
  })
  it('refreshes authoritative assistance after submission when the reveal predates this page', async () => {
    const { controller, client } = await openSession()
    client.answerPractice.mockResolvedValueOnce({ item_id: 'item_one', result: 'correct', feedback: '计算正确', solution_text: null, session_status: 'completed' })
    client.practiceSession.mockResolvedValueOnce(session({ status: 'completed', items: [item({ response: { answer_text: '10', result: 'correct', hint_used: true } })] }))
    controller.changeInput('case_one', { text: '10', selected: [], fields: {} }); await controller.submit('case_one')
    expect(activeEntry(controller).session?.items[0].response?.hint_used).toBe(true)
  })
  it.each(['correct', 'incorrect', 'unscorable'] as const)('keeps %s feedback without automatically revealing the solution', result => {
    const html = renderToStaticMarkup(<NotebookFeedback item={item({ solution_text: '不应自动展示的解析', response: { answer_text: '8', result, hint_used: false, feedback: '这里是批改反馈' } })} />)
    expect(html).toContain('你的作答'); expect(html).toContain('这里是批改反馈'); expect(html).not.toContain('不应自动展示的解析')
    expect(html).toContain(result === 'correct' ? '答对了' : result === 'incorrect' ? '还需要再想一想' : '尚未判定对错')
  })
})

describe('notebook presentation and keyboard controls', () => {
  it('uses the approved labels, keeps full originals and marks previous practice independently', () => {
    expect(notebookStatus).toEqual({ uncorrected: '未纠错', submitted: '已提交（检查中）', correct: '已做对', needs_practice: '仍需练习', check_failed: '检查失败' })
    const text = '完整原题 '.repeat(100) + '2^3'
    const html = renderToStaticMarkup(<NotebookCard item={row({ question_text: text, practiced: true })} onOpen={() => {}} />)
    expect(html).toContain('已练习过'); expect(html).toContain('举一反三练习'); expect(html).toContain('未纠错')
    expect(html).toContain('完整原题 '.repeat(100)); expect(html).toContain('<time'); expect(html).not.toContain('已掌握')
    const math = renderToStaticMarkup(<NotebookCard item={row({ question_text: 'x^2' })} onOpen={() => {}} />)
    expect(math).toContain('role="math"')
  })
  it('renders the exact page heading and only one short subtitle without starting generation', () => {
    const client = clients()
    const html = renderToStaticMarkup(<VariantNotebookPage client={client as unknown as StudentClient} studentId={studentId} initialCaseId="case_one" />)
    expect(html).toContain('>举一反三</h1>'); expect(html).toContain('把没弄懂的题，再练明白一点。')
    expect(client.generateVariant).not.toHaveBeenCalled()
  })
  it('uses real option labels and math rendering and retains structured blank identity', () => {
    const value = { text: '', selected: ['E'], fields: { area: '9' } }
    const html = renderToStaticMarkup(<NotebookAnswerInput item={item({ answer_kind: 'choice', question_kind: 'choice', question_type: 'multiple_choice', options: [{ label: 'A', text: '1/2' }, { label: 'E', text: 'x^2' }] })} value={value} disabled={false} onChange={() => {}} />)
    expect(html.match(/type="checkbox"/g)).toHaveLength(2); expect(html).toContain('role="math"'); expect(html).toContain('checked=""')
    const blanks = renderToStaticMarkup(<NotebookAnswerInput item={item({ question_kind: 'fill_blank', answer_fields: [{ id: 'area', label: '面积' }, { id: 'length', label: '边长' }] })} value={value} disabled={false} onChange={() => {}} />)
    expect(blanks).toContain('value="9"'); expect(blanks).toContain('边长'); expect(blanks.match(/<input/g)).toHaveLength(2)
  })
  it('renders an accessible modal with separate camera and album inputs, exit, and no timeout UI', async () => {
    const { controller, client } = await openSession()
    const html = renderToStaticMarkup(<NotebookModal entry={activeEntry(controller)} controller={controller} client={client as unknown as StudentClient} />)
    expect(html).toContain('role="dialog"'); expect(html).toContain('aria-modal="true"'); expect(html).toContain('aria-labelledby=')
    expect(html.match(/type="file"/g)).toHaveLength(2); expect(html.match(/capture="environment"/g)).toHaveLength(1)
    expect(html).toContain('>退出</button>'); expect(html).toContain('换一道题'); expect(html).not.toContain('剩余'); expect(html).not.toContain('10 分钟')
  })
  it('contains Tab and Shift+Tab at either edge and recovers from outside/disabled focus', () => {
    const first = { focus: vi.fn(), hidden: false, closest: () => null }, last = { focus: vi.fn(), hidden: false, closest: () => null }
    const dialog = { querySelectorAll: () => [first, last], focus: vi.fn() } as unknown as HTMLElement
    const forward = { key: 'Tab', shiftKey: false, preventDefault: vi.fn() }
    trapNotebookFocus(forward, dialog, last as unknown as Element); expect(first.focus).toHaveBeenCalledTimes(1); expect(forward.preventDefault).toHaveBeenCalled()
    trapNotebookFocus({ ...forward, shiftKey: true }, dialog, first as unknown as Element); expect(last.focus).toHaveBeenCalledTimes(1)
    trapNotebookFocus(forward, dialog, null); expect(first.focus).toHaveBeenCalledTimes(2)
    trapNotebookFocus({ ...forward, key: 'ArrowDown' }, dialog, null); expect(first.focus).toHaveBeenCalledTimes(2)
  })
})
