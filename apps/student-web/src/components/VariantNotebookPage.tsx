import { useEffect, useId, useMemo, useRef, useSyncExternalStore, type ChangeEvent } from 'react'
import { Camera, Check, ChevronDown, ChevronRight, ImagePlus, LoaderCircle, RefreshCw, Shuffle, X } from 'lucide-react'
import { PRACTICE_ANSWER_MAX, safeError, StudentError, type PracticeItem, type PracticeSession, type PracticeSubmission, type StudentClient, type VariantGeneration, type VariantNotebookQuestion, type VariantNotebookDraft } from '../core'
import type { NormalizedPhoto } from '../core/types'
import { normalizePhoto } from '../core/photo'
import { applyPracticeAnswer, applyPracticeAssessment, assessmentPending } from '../practice-state'
import { buildPracticeSubmission, practiceSubmissionReady, restoredPracticeInput, type PracticeInputValue } from './PracticeRunner'
import { MathText } from './MathText'
import { useObjectUrl } from './useObjectUrl'
import '../variant-notebook.css'

type NotebookItem = VariantNotebookQuestion
export type NotebookClient = Pick<StudentClient, 'selectedStudentId' | 'historyScope' | 'variantGeneration' | 'startPractice' | 'practiceSession' | 'practiceAssessment' | 'practiceSolution' | 'answerPractice' | 'variantNotebook' | 'generateVariant' | 'retryPractice' | 'answerPracticePhoto' | 'variantDraft' | 'saveVariantDraft'>

export const notebookStatus = {
  uncorrected: '未纠错', submitted: '已提交（检查中）', correct: '已做对', needs_practice: '仍需练习', check_failed: '检查失败',
} satisfies Record<NotebookItem['status'], string>
const blankInput = (): PracticeInputValue => ({ text: '', selected: [], fields: {} })
const pendingGenerationStatus = (status: NotebookItem['generation_status']) => !!status && ['queued', 'generating', 'verifying'].includes(status)
const pendingGeneration = (job: VariantGeneration | null) => pendingGenerationStatus(job?.status)
const waitingForAssessment = (item?: PracticeItem) => assessmentPending(item?.assessment) || item?.assessment?.status === 'completed' && !item.response
const ownedError = () => new StudentError('session_changed', 401)
const pollDelay = 1800

export interface NotebookEntry {
  row: NotebookItem; job: VariantGeneration | null; generationId: string | null; session: PracticeSession | null
  input: PracticeInputValue; photo: NormalizedPhoto | null; regenerateToken: string | null
  busy: boolean; error: string; storageError: string; unconfirmed: boolean; loaded: boolean
  generationRequest?: boolean
  solutionRevision?: number
}
export interface NotebookSnapshot { items: NotebookItem[]; loading: boolean; error: string; activeCase: string | null; entries: Record<string, NotebookEntry> }

/** One student scope. Closing the dialog does not cancel its generation or assessment observer. */
export class VariantNotebookController {
  private snapshot: NotebookSnapshot = { items: [], loading: true, error: '', activeCase: null, entries: {} }
  private listeners = new Set<() => void>()
  private alive = false
  private epoch = 0
  private readVersion = 0
  private timer: ReturnType<typeof setTimeout> | undefined
  private listTimer: ReturnType<typeof setTimeout> | undefined
  private listFlight: { epoch: number; promise: Promise<void> } | null = null
  private saveQueues = new Map<string, Promise<unknown>>()
  private revealedSolutions = new Set<string>()
  private readonly scope: string | null
  constructor(readonly client: NotebookClient, readonly studentId: string) { this.scope = client.historyScope }
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener) } }
  getSnapshot = () => this.snapshot
  private valid(epoch = this.epoch) { return this.alive && epoch === this.epoch && this.client.selectedStudentId === this.studentId && this.client.historyScope === this.scope }
  private publish(patch: Partial<NotebookSnapshot> = {}) {
    if (!this.valid()) return
    this.snapshot = { ...this.snapshot, ...patch, entries: { ...this.snapshot.entries } }
    this.listeners.forEach(listener => listener())
  }
  start() { this.alive = true; this.epoch++; void this.refresh(); this.schedule(); this.scheduleList() }
  dispose() { this.alive = false; this.epoch++; this.readVersion++; clearTimeout(this.timer); clearTimeout(this.listTimer) }
  refresh(): Promise<void> {
    if (!this.valid()) return Promise.resolve()
    if (this.listFlight?.epoch === this.epoch) return this.listFlight.promise
    const epoch = this.epoch
    const promise = this.readNotebook().finally(() => { if (this.listFlight?.promise === promise) this.listFlight = null })
    this.listFlight = { epoch, promise }
    return promise
  }
  private async readNotebook() {
    const epoch = this.epoch, version = ++this.readVersion
    try {
      const next = await this.client.variantNotebook()
      if (!this.valid(epoch) || version !== this.readVersion) return
      if (next.student_id !== this.studentId) throw ownedError()
      this.publish({ items: next.items, loading: false, error: '' })
    } catch (error) {
      if (this.valid(epoch) && version === this.readVersion) this.publish({ loading: false, error: safeError(error).message })
    }
  }
  private entry(caseId: string) { return this.snapshot.entries[caseId] }
  private save(entry: NotebookEntry): Promise<unknown> {
    const epoch = this.epoch, caseId = entry.row.case_id
    const draft: VariantNotebookDraft = { generation_id: entry.generationId, session_id: entry.session?.id ?? null,
      regenerate_token: entry.regenerateToken, input: entry.input, photo: entry.photo }
    // Serialize storage writes so slow photo saves cannot replace newer typed answers.
    const task = (this.saveQueues.get(caseId) ?? Promise.resolve()).catch(() => undefined).then(async () => {
      if (!this.valid(epoch)) return
      await this.client.saveVariantDraft(caseId, draft)
      if (this.valid(epoch)) { entry.storageError = ''; this.publish() }
    })
    this.saveQueues.set(caseId, task)
    void task.catch(() => {
      if (this.valid(epoch)) { entry.storageError = '草稿暂未保存到本机，请保持此页面打开并重试。'; this.publish() }
    })
    return task
  }
  saveDraft(caseId: string) { const entry = this.entry(caseId); if (entry && this.valid()) void this.save(entry).catch(() => undefined) }
  changeInput(caseId: string, input: PracticeInputValue) {
    const entry = this.entry(caseId)
    if (!entry || entry.busy || !this.valid()) return
    entry.input = input; this.publish(); this.saveDraft(caseId)
  }
  removePhoto(caseId: string) {
    const entry = this.entry(caseId)
    if (!entry || entry.busy || !this.valid()) return
    entry.photo = null; this.publish(); this.saveDraft(caseId)
  }
  private async act(entry: NotebookEntry, action: (current: () => boolean) => Promise<void>) {
    if (!this.valid() || entry.busy) return
    const epoch = this.epoch
    entry.busy = true; entry.error = ''; this.publish()
    const current = () => this.valid(epoch) && this.entry(entry.row.case_id) === entry
    try { await action(current) }
    catch (error) { if (current()) entry.error = safeError(error).message }
    finally { if (current()) { entry.busy = false; entry.generationRequest = false; this.publish() } }
  }
  open(row: NotebookItem) {
    if (!this.valid()) return
    let entry = this.entry(row.case_id)
    const newerGeneration = entry && row.generation_id && row.generation_id !== entry.row.generation_id && row.generation_id !== entry.generationId
    const newerSession = entry && row.session_id && row.session_id !== entry.row.session_id && row.session_id !== entry.session?.id
    if (!entry || !entry.regenerateToken && (newerGeneration || newerSession)) {
      entry = { row, job: null, generationId: row.generation_id, session: null, input: blankInput(), photo: null, regenerateToken: null,
        busy: false, error: '', storageError: '', unconfirmed: false, loaded: false }
      this.snapshot.entries[row.case_id] = entry
    }
    entry.row = row
    this.publish({ activeCase: row.case_id })
    return this.prepare(row.case_id)
  }
  close() { this.publish({ activeCase: null }) }
  solutionCurrent(caseId: string, sessionId: string, itemId: string) {
    const session = this.entry(caseId)?.session
    return this.valid() && this.snapshot.activeCase === caseId && session?.id === sessionId && session.items[0]?.id === itemId
  }
  solutionWaiting(caseId: string) { return waitingForAssessment(this.entry(caseId)?.session?.items[0]) }
  async solution(caseId: string, sessionId: string, itemId: string) {
    if (!this.solutionCurrent(caseId, sessionId, itemId)) throw ownedError()
    if (this.solutionWaiting(caseId)) throw new StudentError('invalid_request', 409)
    const epoch = this.epoch, entry = this.entry(caseId)
    const solution = await this.client.practiceSolution(sessionId, itemId)
    if (!this.valid(epoch) || this.entry(caseId) !== entry || entry?.session?.id !== sessionId || entry.session.items[0]?.id !== itemId) throw ownedError()
    if (solution.item_id !== itemId) throw new StudentError('invalid_response', 502)
    // Keep the assistance receipt across dialog closes, without marking the question practiced.
    this.revealedSolutions.add(JSON.stringify([sessionId, itemId]))
    return solution
  }
  private acceptSession(entry: NotebookEntry, session: PracticeSession) {
    if (session.student_id !== this.studentId || session.purpose !== 'variant') throw ownedError()
    if (entry.job?.item_id && !session.items.some(item => item.id === entry.job!.item_id)) throw new StudentError('invalid_response', 502)
    entry.session = session; entry.unconfirmed = false
    if (!entry.input.text && !entry.input.selected.length && !Object.values(entry.input.fields).some(Boolean) && session.items[0]) entry.input = restoredPracticeInput(session.items[0])
  }
  private acceptJob(entry: NotebookEntry, job: VariantGeneration) {
    if (job.student_id !== this.studentId || job.source_case_id !== entry.row.case_id) throw ownedError()
    entry.job = job; entry.generationId = job.job_id
  }
  private async finishGeneration(entry: NotebookEntry, current: () => boolean) {
    const job = entry.job
    if (!job || !current()) return
    if (job.session_id || job.status === 'ready') {
      const session = job.session_id ? await this.client.practiceSession(job.session_id)
        : await this.client.startPractice({ purpose: 'variant', willing: true, generation_job_id: job.job_id, count: 1 })
      if (!current()) return
      this.acceptSession(entry, session); entry.regenerateToken = null
      this.saveDraft(entry.row.case_id); void this.refresh()
    }
  }
  async prepare(caseId: string) {
    const entry = this.entry(caseId)
    if (!entry) return
    await this.act(entry, async current => {
      if (!entry.loaded) {
        const draft = await this.client.variantDraft(caseId)
        if (!current()) return
        const sameGeneration = !entry.row.generation_id || (draft?.generation_id ? entry.row.generation_id === draft.generation_id : !!entry.row.session_id && entry.row.session_id === draft?.session_id)
        if (draft && (sameGeneration || draft.regenerate_token)) { entry.input = draft.input; entry.photo = draft.photo; entry.regenerateToken = draft.regenerate_token }
        const generationId = draft?.regenerate_token ? draft.generation_id : entry.row.generation_id ?? draft?.generation_id
        const sessionId = draft?.regenerate_token ? null : entry.row.session_id ?? (sameGeneration ? draft?.session_id : null)
        entry.generationId = generationId ?? null
        if (sessionId) {
          const session = await this.client.practiceSession(sessionId)
          if (!current()) return
          this.acceptSession(entry, session); entry.loaded = true; this.saveDraft(caseId); return
        }
        if (generationId) {
          const job = await this.client.variantGeneration(generationId)
          if (!current()) return
          this.acceptJob(entry, job)
        }
        entry.loaded = true
      }
      if (entry.session) {
        const session = await this.client.practiceSession(entry.session.id)
        if (!current()) return
        this.acceptSession(entry, session); void this.refresh(); return
      }
      if (!entry.job || ['failed', 'superseded'].includes(entry.job.status)) {
        entry.generationRequest = true; this.publish()
        const job = await this.client.generateVariant({ willing: true, count: 1, source_case_id: caseId,
          ...(entry.regenerateToken ? { regenerate_token: entry.regenerateToken } : {}) })
        if (!current()) return
        this.acceptJob(entry, job); entry.generationRequest = false; this.saveDraft(caseId)
      }
      await this.finishGeneration(entry, current)
    })
  }
  async regenerate(caseId: string) {
    const entry = this.entry(caseId)
    if (!entry || entry.busy || !this.valid()) return
    await this.act(entry, async current => {
      entry.regenerateToken = crypto.randomUUID(); entry.job = null; entry.generationId = null; entry.session = null
      entry.generationRequest = true
      entry.input = blankInput(); entry.photo = null; entry.unconfirmed = false; entry.loaded = true
      this.publish(); await this.save(entry)
      if (!current()) return
      const job = await this.client.generateVariant({ willing: true, count: 1, source_case_id: caseId, regenerate_token: entry.regenerateToken! })
      if (!current()) return
      this.acceptJob(entry, job); entry.generationRequest = false; this.saveDraft(caseId); await this.finishGeneration(entry, current)
    })
  }
  async choosePhoto(caseId: string, file: File) {
    const entry = this.entry(caseId)
    if (!entry) return
    await this.act(entry, async current => {
      const photo = await normalizePhoto(file)
      if (current()) { entry.photo = photo; await this.save(entry) }
    })
  }
  async retry(caseId: string) {
    const entry = this.entry(caseId), session = entry?.session
    if (!entry || !session) return
    await this.act(entry, async current => {
      entry.solutionRevision = (entry.solutionRevision ?? 0) + 1; this.publish()
      const next = await this.client.retryPractice(session.id)
      if (!current()) return
      this.acceptSession(entry, next); await this.save(entry); void this.refresh()
    })
  }
  async submit(caseId: string) {
    const entry = this.entry(caseId), session = entry?.session, item = session?.items[0]
    if (!entry || !session || !item || item.response || entry.unconfirmed || assessmentPending(item.assessment) || item.assessment?.status === 'completed' || item.assessment?.status === 'superseded') return
    const submission = entry.photo ? { answer_text: '照片作答' } : buildPracticeSubmission(item, entry.input)
    if (!entry.photo && !practiceSubmissionReady(submission)) return
    await this.act(entry, async current => {
      if (item.assessment?.retryable) { entry.solutionRevision = (entry.solutionRevision ?? 0) + 1; this.publish() }
      await this.save(entry)
      if (!current()) return
      try {
        const answer = entry.photo ? await this.client.answerPracticePhoto(session.id, item.id, entry.photo)
          : await this.client.answerPractice(session.id, item.id, submission)
        if (!current()) return
        entry.session = applyPracticeAnswer(session, session.id, item.id, answer, submission, this.revealedSolutions.has(JSON.stringify([session.id, item.id])))
        if (answer.result !== 'pending' && (answer.retryable || answer.result === 'unscorable')) entry.error = '这次批改未完成，尚未判定对错。请重试批改。'
        if (answer.result !== 'pending' && !answer.retryable) {
          // The server also knows about assistance before reloads or a lost solution receipt.
          try {
            const next = await this.client.practiceSession(session.id)
            if (current() && next.id === session.id && next.items.find(value => value.id === item.id)?.response) this.acceptSession(entry, next)
          } catch { /* Keep the confirmed answer; a later session read can reconcile assistance. */ }
        }
        void this.refresh()
      } catch (error) {
        if (!current()) return
        entry.unconfirmed = true
        // A lost response may already have created an assessment. GET before allowing another POST.
        try {
          const next = await this.client.practiceSession(session.id)
          if (current()) this.acceptSession(entry, next)
        } catch { /* Keep submission locked until a read confirms server state. */ }
        throw error
      }
    })
  }
  private schedule() {
    clearTimeout(this.timer)
    if (this.valid()) this.timer = setTimeout(() => void this.poll(), pollDelay)
  }
  private scheduleList() {
    clearTimeout(this.listTimer)
    const epoch = this.epoch
    if (this.valid(epoch)) this.listTimer = setTimeout(() => {
      void this.refresh().finally(() => { if (this.valid(epoch)) this.scheduleList() })
    }, 6000)
  }
  async poll() {
    const epoch = this.epoch
    if (!this.valid(epoch)) return
    await Promise.allSettled(Object.values(this.snapshot.entries).map(async entry => {
      if (entry.busy) return
      const item = entry.session?.items[0], assessment = item?.assessment
      const watchSession = entry.session && (entry.unconfirmed || (!item?.response && !!assessment && (assessmentPending(assessment) || assessment.status === 'completed')))
      if (!pendingGeneration(entry.job) && !watchSession) return
      await this.act(entry, async current => {
        if (!entry.session && entry.job && pendingGeneration(entry.job)) {
          const job = await this.client.variantGeneration(entry.job.job_id)
          if (!current()) return
          this.acceptJob(entry, job); await this.finishGeneration(entry, current)
        } else if (entry.session) {
          if (assessment && assessmentPending(assessment)) {
            const next = await this.client.practiceAssessment(assessment.assessment_id, entry.session.id, assessment.item_id)
            if (!current()) return
            entry.session = applyPracticeAssessment(entry.session, next)
            if (assessmentPending(next)) return
          }
          const session = await this.client.practiceSession(entry.session.id)
          if (!current()) return
          this.acceptSession(entry, session); void this.refresh()
        }
      })
    }))
    if (this.valid(epoch)) this.schedule()
  }
}

function formatDate(value: string) {
  const date = new Date(value)
  return Number.isFinite(date.getTime()) ? new Intl.DateTimeFormat('zh-CN', { year: 'numeric', month: 'long', day: 'numeric' }).format(date) : '日期暂缺'
}
export function notebookGenerationStatus(item: NotebookItem, entry?: NotebookEntry): NotebookItem['generation_status'] {
  if (entry?.row.case_id !== item.case_id) return item.generation_status
  // Show the click immediately, before the POST receipt or next list refresh.
  if (entry.generationRequest) return 'generating'
  const local = entry.job
  const newerServerGeneration = item.generation_id && item.generation_id !== local?.job_id && item.generation_id !== entry.row.generation_id
  if (!local || newerServerGeneration) return item.generation_status
  // A terminal read beats a delayed queued/running read of the same job.
  if (item.generation_id === local.job_id && item.generation_status && !pendingGenerationStatus(item.generation_status)) return item.generation_status
  return local.status
}

export function NotebookCard({ item, entry, onOpen }: { item: NotebookItem; entry?: NotebookEntry; onOpen: () => void }) {
  const generationStatus = notebookGenerationStatus(item, entry), generating = pendingGenerationStatus(generationStatus)
  const status = generating ? 'generating' : generationStatus === 'failed' ? 'generation_failed' : generationStatus === 'superseded' ? 'generation_superseded' : item.status
  const label = generating ? '生成中' : generationStatus === 'failed' ? '生成未完成' : generationStatus === 'superseded' ? '需重新生成' : notebookStatus[item.status]
  return <article className="notebook-card">
    <div className="notebook-card-meta"><time dateTime={item.occurred_at}>{formatDate(item.occurred_at)}</time><span className={`notebook-status ${status}`} role="status">{generating && <LoaderCircle className="spin" size={12} aria-hidden="true" />}{label}</span></div>
    {(item.homework_title || item.practiced) && <p className="notebook-homework">{item.homework_title}{item.practiced && <span className="notebook-practiced">已练习过</span>}</p>}
    <p className="notebook-original"><MathText text={item.question_text || '原题文字暂缺'} /></p>
    {item.original_status && <p className="notebook-original-status" role="status">{item.original_status === 'rechecking' ? '原题正在重新检查，已有练习记录会保留。' : '原题待老师核实，已有练习记录会保留。'}</p>}
    <button className="button secondary notebook-open" disabled={!!item.original_status && !item.session_id && !item.generation_id && !entry} onClick={onOpen}>举一反三练习<ChevronRight size={16} aria-hidden="true" /></button>
  </article>
}

export function NotebookAnswerInput({ item, value, disabled, onChange }: { item: PracticeItem; value: PracticeInputValue; disabled: boolean; onChange: (value: PracticeInputValue) => void }) {
  const id = useId()
  if (item.question_kind === 'choice' || item.answer_kind === 'choice') {
    const multiple = item.question_type === 'multiple_choice', options = item.options?.length ? item.options : ['A', 'B', 'C', 'D'].map(label => ({ label, text: '' }))
    return <fieldset className="notebook-options" disabled={disabled}><legend>{multiple ? '选择所有正确选项' : '选择一个答案'}</legend>{options.map(option => <label key={option.label} className={value.selected.includes(option.label) ? 'selected' : ''}>
      <input type={multiple ? 'checkbox' : 'radio'} name={id} checked={value.selected.includes(option.label)} onChange={() => onChange({ ...value, selected: multiple ? value.selected.includes(option.label) ? value.selected.filter(label => label !== option.label) : [...value.selected, option.label] : [option.label] })} />
      <strong>{option.label}</strong><span><MathText text={option.text} /></span>
    </label>)}</fieldset>
  }
  if (item.question_kind === 'fill_blank' && item.answer_fields?.length) return <fieldset className="notebook-blanks" disabled={disabled}><legend>逐空填写</legend>{item.answer_fields.map((field, index) => <label key={field.id} htmlFor={`${id}-${index}`}><span><MathText text={field.label || `第 ${index + 1} 空`} /></span><input id={`${id}-${index}`} maxLength={PRACTICE_ANSWER_MAX} value={value.fields[field.id] ?? ''} onChange={event => onChange({ ...value, fields: { ...value.fields, [field.id]: event.target.value } })} /></label>)}</fieldset>
  return <label className="notebook-text-answer" htmlFor={id}><span>你的答案与过程</span><textarea id={id} rows={5} maxLength={PRACTICE_ANSWER_MAX} value={value.text} disabled={disabled} onChange={event => onChange({ ...value, text: event.target.value })} placeholder="写下答案，也可以拍下纸上的解题过程。" /></label>
}

export function NotebookFeedback({ item }: { item: PracticeItem }) {
  if (!item.response) {
    const assessment = item.assessment
    if (!assessment) return null
    return <div className={`notebook-feedback ${assessment.status === 'failed' ? 'check_failed' : ''}`} role="status">
      <strong>{assessmentPending(assessment) ? '正在批改，你的作答已保存' : assessment.status === 'completed' ? '正在读取批改结果' : '这次批改未完成，尚未判定对错'}</strong>
      {assessment.feedback && <p><MathText text={assessment.feedback} /></p>}
    </div>
  }
  const result = item.response.result, correct = result === 'correct', unscored = result === 'unscorable'
  return <div className={`notebook-feedback ${correct ? 'correct' : unscored ? 'check_failed' : 'needs_practice'}`} role="status">
    <strong>{correct ? '答对了' : unscored ? '这次批改未完成，尚未判定对错' : '还需要再想一想'}</strong>
    <p className="notebook-submitted"><span>你的作答</span><MathText text={item.response.answer_text} /></p>
    {item.response.feedback && <p><MathText text={item.response.feedback} /></p>}
  </div>
}

type NotebookSolutionValue = Awaited<ReturnType<NotebookClient['practiceSolution']>>
interface NotebookSolutionSnapshot { expanded: boolean; loading: boolean; error: string; solution: NotebookSolutionValue | null }
const blankSolution = (): NotebookSolutionSnapshot => ({ expanded: false, loading: false, error: '', solution: null })

/** One disclosure lifetime: polls keep it; closing, changing questions and retries remount it. */
export class NotebookSolutionController {
  private snapshot = blankSolution()
  private listeners = new Set<() => void>()
  private alive = false
  private epoch = 0
  private flight: Promise<void> | null = null
  constructor(private notebook: VariantNotebookController, private caseId: string, private sessionId: string, private itemId: string) {}
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener) } }
  getSnapshot = () => this.snapshot
  private valid(epoch = this.epoch) { return this.alive && epoch === this.epoch && this.notebook.solutionCurrent(this.caseId, this.sessionId, this.itemId) }
  private publish(patch: Partial<NotebookSolutionSnapshot>) {
    this.snapshot = { ...this.snapshot, ...patch }
    this.listeners.forEach(listener => listener())
  }
  start() { this.alive = true; this.epoch++ }
  dispose() { this.alive = false; this.epoch++; this.flight = null; this.snapshot = blankSolution() }
  open(): Promise<void> {
    if (!this.valid() || !this.snapshot.solution && this.notebook.solutionWaiting(this.caseId)) return Promise.resolve()
    this.publish({ expanded: true })
    return this.snapshot.solution || this.snapshot.error ? Promise.resolve() : this.load()
  }
  close() { if (this.valid()) this.publish({ expanded: false }) }
  retry(): Promise<void> {
    return this.valid() && !this.notebook.solutionWaiting(this.caseId) && this.snapshot.expanded && !this.snapshot.solution ? this.load() : Promise.resolve()
  }
  private load(): Promise<void> {
    if (this.flight) return this.flight
    const epoch = this.epoch
    this.publish({ loading: true, error: '' })
    const promise = this.notebook.solution(this.caseId, this.sessionId, this.itemId).then(solution => {
      if (this.valid(epoch)) this.publish({ solution, loading: false })
    }, () => {
      if (this.valid(epoch)) this.publish({ error: '答案暂时没加载出来，请重试。', loading: false })
    }).finally(() => { if (this.flight === promise) this.flight = null })
    this.flight = promise
    return promise
  }
}

export function NotebookSolutionPanel({ controller, waiting = false }: { controller: NotebookSolutionController; waiting?: boolean }) {
  const state = useSyncExternalStore(controller.subscribe, controller.getSnapshot, controller.getSnapshot), contentId = useId()
  const blocked = waiting && !state.solution
  useEffect(() => { controller.start(); return () => controller.dispose() }, [controller])
  return <section className="notebook-solution">
    <button type="button" className="text-button notebook-solution-toggle" aria-expanded={state.expanded} aria-controls={contentId} disabled={blocked && !state.expanded} onClick={() => { if (state.expanded) controller.close(); else void controller.open() }}>
      {state.expanded ? <ChevronDown size={17} aria-hidden="true" /> : <ChevronRight size={17} aria-hidden="true" />}{blocked ? '批改完成后可查看答案' : '查看答案与步骤'}
    </button>
    <div id={contentId} hidden={!state.expanded} className="notebook-solution-content">
      {state.expanded && <>
        {state.loading && <p role="status"><LoaderCircle className="spin" size={15} aria-hidden="true" />正在加载答案…</p>}
        {state.error && <div className="notebook-solution-error" role="alert"><p>{state.error}</p><button type="button" className="text-button" disabled={blocked} onClick={() => void controller.retry()}>重试</button></div>}
        {state.solution && <><p><span className="notebook-solution-label">正确答案</span><MathText text={state.solution.answer_text} /></p><p><span className="notebook-solution-label">解题步骤</span><MathText text={state.solution.solution_text} /></p></>}
      </>}
    </div>
  </section>
}

function NotebookSolution({ notebook, caseId, sessionId, itemId, waiting }: { notebook: VariantNotebookController; caseId: string; sessionId: string; itemId: string; waiting: boolean }) {
  const controller = useMemo(() => new NotebookSolutionController(notebook, caseId, sessionId, itemId), [notebook, caseId, sessionId, itemId])
  return <NotebookSolutionPanel controller={controller} waiting={waiting} />
}

const focusableSelector = 'button:not(:disabled), input:not(:disabled):not([type="hidden"]), textarea:not(:disabled), select:not(:disabled), summary, a[href], [tabindex="0"]'
/** Keyboard containment also handles focus on the dialog itself or on a control that just became disabled. */
export function trapNotebookFocus(event: Pick<KeyboardEvent, 'key' | 'shiftKey' | 'preventDefault'>, dialog: HTMLElement, active: Element | null) {
  if (event.key !== 'Tab') return
  const controls = Array.from(dialog.querySelectorAll<HTMLElement>(focusableSelector)).filter(node => !node.hidden && !node.closest('[hidden]'))
  const first = controls[0], last = controls.at(-1)
  if (!first) { event.preventDefault(); dialog.focus(); return }
  if (!active || !controls.includes(active as HTMLElement) || event.shiftKey && active === first || !event.shiftKey && active === last) {
    event.preventDefault(); (event.shiftKey ? last! : first).focus()
  }
}

export function NotebookModal({ entry, controller, client }: { entry: NotebookEntry; controller: VariantNotebookController; client: StudentClient }) {
  const titleId = useId(), descriptionId = useId(), dialog = useRef<HTMLDivElement>(null), camera = useRef<HTMLInputElement>(null), album = useRef<HTMLInputElement>(null)
  const caseId = entry.row.case_id, item = entry.session?.items[0]
  const imageUrl = useObjectUrl(client, entry.photo?.blob ?? null)
  useEffect(() => {
    const node = dialog.current
    if (!node) return
    const previous = document.activeElement instanceof HTMLElement && document.activeElement !== document.body ? document.activeElement : document.getElementById('variant-notebook-title')
    const alreadyLocked = document.body.classList.contains('notebook-modal-open')
    document.body.classList.add('notebook-modal-open')
    const initial = node.querySelector<HTMLButtonElement>('.notebook-close')
    initial?.focus()
    const keydown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); controller.close() }
      else trapNotebookFocus(event, node, document.activeElement)
    }
    const focusin = (event: FocusEvent) => { if (!node.contains(event.target as Node)) initial?.focus() }
    document.addEventListener('keydown', keydown, true); document.addEventListener('focusin', focusin)
    return () => {
      document.removeEventListener('keydown', keydown, true); document.removeEventListener('focusin', focusin)
      if (!alreadyLocked) document.body.classList.remove('notebook-modal-open')
      if (previous?.isConnected) previous.focus()
    }
  }, [controller])
  const assessment = item?.assessment
  const waiting = waitingForAssessment(item)
  const disabled = entry.busy || entry.unconfirmed || waiting || assessment?.status === 'superseded' || !!item?.response
  const submission: PracticeSubmission | null = item ? buildPracticeSubmission(item, entry.input) : null
  const photoChange = (event: ChangeEvent<HTMLInputElement>) => { const file = event.target.files?.[0]; event.target.value = ''; if (file) void controller.choosePhoto(caseId, file) }
  const retryResponse = item?.response && item.response.result !== 'correct'
  const generationFailed = entry.job?.status === 'failed' || entry.job?.status === 'superseded'
  return <div className="notebook-overlay" onClick={event => { if (event.target === event.currentTarget) controller.close() }}>
    <div ref={dialog} className="notebook-dialog" role="dialog" aria-modal="true" aria-labelledby={titleId} aria-describedby={descriptionId} tabIndex={-1}>
      <header className="notebook-dialog-heading"><div><h2 id={titleId}>举一反三练习</h2><p id={descriptionId}>一次一道，随时退出，下次接着练。</p></div><button className="icon-button notebook-close" aria-label="退出" onClick={() => controller.close()}><X size={21} aria-hidden="true" /></button></header>
      <div className="notebook-dialog-body">
        <details className="notebook-source"><summary>查看原题</summary><p><MathText text={entry.row.question_text || '原题文字暂缺'} /></p></details>
        {entry.error && <div className="notebook-error" role="alert"><p>{entry.error}</p><button className="text-button" disabled={entry.busy} onClick={() => void controller.prepare(caseId)}><RefreshCw size={15} aria-hidden="true" />{entry.session ? '刷新提交状态' : '重试准备'}</button></div>}
        {entry.storageError && <div className="notebook-error" role="alert"><p>{entry.storageError}</p><button className="text-button" onClick={() => controller.saveDraft(caseId)}>重试保存草稿</button></div>}
        {!item && <div className="notebook-preparing" role="status" aria-live="polite">
          {!generationFailed && !entry.error && <LoaderCircle size={26} className="spin" aria-hidden="true" />}
          <p>{generationFailed ? '这次还没准备好题目，可以重试。' : entry.error ? '准备暂时中断，进度会保留。' : entry.job?.status === 'verifying' ? '正在核对题目与答案…' : entry.job?.status === 'generating' ? '正在根据原题准备一道新题…' : '正在准备你的练习…'}</p>
          {generationFailed && <button className="button secondary" disabled={entry.busy} onClick={() => void controller.prepare(caseId)}>重试准备</button>}
        </div>}
        {item && <>
          <p className="notebook-practice-stem"><MathText text={item.stem_text} /></p>
          <NotebookFeedback item={item} />
          {item.response && !!item.options?.length && <ul className="notebook-reviewed-options">{item.options.map(option => <li key={option.label}><strong>{option.label}</strong><MathText text={option.text} /></li>)}</ul>}
          {!item.response && <form className="notebook-answer-form" onSubmit={event => { event.preventDefault(); if (!disabled) void controller.submit(caseId) }}>
            <NotebookAnswerInput item={item} value={entry.input} disabled={!!disabled || !!entry.photo} onChange={value => controller.changeInput(caseId, value)} />
            <div className="notebook-photo-actions"><button type="button" className="button secondary" disabled={!!disabled} onClick={() => camera.current?.click()}><Camera size={17} aria-hidden="true" />拍照作答</button><button type="button" className="button secondary" disabled={!!disabled} onClick={() => album.current?.click()}><ImagePlus size={17} aria-hidden="true" />从相册选择</button></div>
            <input ref={camera} type="file" accept="image/jpeg,image/png,image/webp" capture="environment" hidden aria-label="拍照作答" onChange={photoChange} />
            <input ref={album} type="file" accept="image/jpeg,image/png,image/webp" hidden aria-label="从相册选择作答照片" onChange={photoChange} />
            {entry.photo && <div className="notebook-photo-preview">{imageUrl && <img src={imageUrl} alt="待提交的作答照片" />}<p>本次将提交这张照片。</p><button className="text-button" type="button" disabled={!!disabled} onClick={() => controller.removePhoto(caseId)}>移除照片，改用文字</button></div>}
            <button className="button primary notebook-submit" type="submit" disabled={!!disabled || !entry.photo && (!submission || !practiceSubmissionReady(submission))}>{entry.busy || waiting ? <LoaderCircle className="spin" size={17} aria-hidden="true" /> : <Check size={17} aria-hidden="true" />}{waiting ? '正在批改' : entry.unconfirmed ? '等待确认提交状态' : assessment?.retryable ? '重试批改' : entry.busy ? '处理中…' : '提交答案'}</button>
          </form>}
          {retryResponse && <button className="button primary notebook-retry" disabled={entry.busy} onClick={() => void controller.retry(caseId)}><RefreshCw size={17} aria-hidden="true" />{item.response!.result === 'unscorable' ? '重试批改' : '再试一次'}</button>}
          {entry.session && <NotebookSolution key={JSON.stringify([entry.session.id, item.id, entry.solutionRevision ?? 0])} notebook={controller} caseId={caseId} sessionId={entry.session.id} itemId={item.id} waiting={waiting} />}
        </>}
      </div>
      <footer className="notebook-dialog-footer"><button className="button secondary" onClick={() => controller.close()}>退出</button><button className="text-button" disabled={entry.busy || !!waiting || entry.unconfirmed || !!entry.regenerateToken || !item} onClick={() => void controller.regenerate(caseId)}><Shuffle size={16} aria-hidden="true" />换一道题</button></footer>
    </div>
  </div>
}

function NotebookWorkspace({ client, studentId, initialCaseId }: { client: StudentClient; studentId: string; initialCaseId?: string | null }) {
  const controller = useMemo(() => new VariantNotebookController(client, studentId), [client, studentId])
  const state = useSyncExternalStore(controller.subscribe, controller.getSnapshot, controller.getSnapshot)
  const openedInitial = useRef<string | null>(null)
  useEffect(() => { controller.start(); return () => controller.dispose() }, [controller])
  useEffect(() => {
    if (!initialCaseId || openedInitial.current === initialCaseId) return
    const item = state.items.find(row => row.case_id === initialCaseId)
    if (item) { openedInitial.current = initialCaseId; controller.open(item) }
  }, [controller, initialCaseId, state.items])
  const active = state.activeCase ? state.entries[state.activeCase] : null
  return <section className="variant-notebook" aria-labelledby="variant-notebook-title">
    <div className="notebook-page-content" inert={!!active}>
      <header className="notebook-heading"><h1 id="variant-notebook-title" tabIndex={-1}>举一反三</h1><p>把没弄懂的题，再练明白一点。</p></header>
      {state.error && <div className="notebook-error" role="alert"><p>{state.error}</p><button className="text-button" onClick={() => void controller.refresh()}>重新加载</button></div>}
      {state.loading ? <p className="notebook-empty" role="status">正在打开错题本…</p> : state.items.length ? <div className="notebook-grid">{state.items.map(item => <NotebookCard key={item.case_id} item={item} entry={state.entries[item.case_id]} onOpen={() => controller.open(item)} />)}</div> : !state.error && <p className="notebook-empty">还没有错题，完成作业后会在这里留下记录。</p>}
      <span className="notebook-sr-only" role="status" aria-live="polite">{!state.loading && !state.error ? `共 ${state.items.length} 道错题` : ''}</span>
    </div>
    {active && <NotebookModal key={active.row.case_id} entry={active} controller={controller} client={client} />}
  </section>
}

export function VariantNotebookPage(props: { client: StudentClient; studentId: string; initialCaseId?: string | null }) {
  const scope = useSyncExternalStore(listener => props.client.subscribe(listener), () => props.client.historyScope, () => props.client.historyScope)
  if (props.client.selectedStudentId !== props.studentId) return <p role="status">正在切换学生…</p>
  return <NotebookWorkspace key={JSON.stringify([props.studentId, scope])} {...props} />
}
export default VariantNotebookPage
