import { useCallback, useEffect, useId, useRef, useState, type FormEvent } from 'react'
import { ArrowLeft, Check, Clock3, Lightbulb, LoaderCircle, LogOut, RefreshCw } from 'lucide-react'
import { PRACTICE_ANSWER_MAX, safeError, StudentError, type ExitReason, type PracticeAssessment, type PracticeItem, type PracticeSession, type PracticeSubmission, type PreviewTarget } from '../core'
import type { Workspace } from '../useStudentWorkspace'
import { applyPracticeAnswer, applyPracticeAssessment, assessmentPending } from '../practice-state'
import { answerFeedback, choiceLetters, effectiveStatus, exitChoices, practiceMessage, readingDone, remainingLabel, statusLabels, wrapUps } from '../learning'

const SESSION_READ_ERROR = '暂时没有读到最新的核验状态，已提交的答案会保留。请刷新查看，不必重复提交。'
const ASSESSMENT_READ_ERROR = '核验状态暂时没有读到。你的作答已保存，可以刷新查看，不需要重新提交。'

export function PreviewReading({ targets }: { targets: PreviewTarget[] }) {
  if (!targets.length) return null
  return <section className="preview-reading" aria-labelledby="preview-title">
    <h2 id="preview-title">下一课要学的</h2>
    <p className="muted small">这些是参考，不是考试。</p>
    {targets.map(target => <article key={target.id} className="preview-target">
      <h3>{target.name}</h3>
      {target.objective && <p><span className="muted small">学完能做到</span><br />{target.objective}</p>}
      {target.prerequisites.length > 0 && <><span className="muted small">会用到之前学过的</span><ul className="prerequisite-list">{target.prerequisites.map(p => <li key={p.id}><span>{p.name}</span><span className={`status-pill ${statusLabels[p.learning_status].tone}`}>{statusLabels[p.learning_status].label}</span></li>)}</ul></>}
    </article>)}
  </section>
}

export interface PracticeInputValue { text: string; selected: string[]; fields: Record<string, string> }
export function buildPracticeSubmission(item: PracticeItem, value: PracticeInputValue): PracticeSubmission {
  if (item.question_kind === 'choice' || item.answer_kind === 'choice') {
    const labels = item.options?.length ? item.options.map(option => option.label) : [...choiceLetters]
    const selected = labels.filter(label => value.selected.includes(label))
    return { answer_text: selected.join(', '), selected_options: selected }
  }
  if (item.question_kind === 'fill_blank' && item.answer_fields?.length) {
    const answers = item.answer_fields.map(field => ({ field_id: field.id, answer_text: (value.fields[field.id] ?? '').trim() }))
    return { answer_text: answers.map((answer, index) => `${item.answer_fields![index].label || `第 ${index + 1} 空`}：${answer.answer_text}`).join('\n'), answers }
  }
  return { answer_text: value.text.trim() }
}
export function practiceSubmissionReady(submission: PracticeSubmission) {
  return !!submission.answer_text.trim() && [...submission.answer_text].length <= PRACTICE_ANSWER_MAX &&
    (!submission.selected_options || submission.selected_options.length > 0) &&
    (!submission.answers || submission.answers.every(answer => !!answer.answer_text.trim()))
}

export function AnswerInput({ item, value, disabled, onChange }: { item: PracticeItem; value: PracticeInputValue; disabled: boolean; onChange: (value: PracticeInputValue) => void }) {
  const id = useId()
  if (item.question_kind === 'choice' || item.answer_kind === 'choice') {
    const multiple = item.question_type === 'multiple_choice'
    const options = item.options?.length ? item.options : choiceLetters.map(label => ({ label, text: '' }))
    return <fieldset className="choice-field" disabled={disabled}><legend>{multiple ? '选择所有符合条件的答案' : '选一个答案'}</legend><div className={`choice-options ${item.options?.length ? 'choice-with-text' : ''}`}>{options.map(option => <label key={option.label} className={value.selected.includes(option.label) ? 'selected' : ''}><input type={multiple ? 'checkbox' : 'radio'} name={`${id}-choice`} value={option.label} checked={value.selected.includes(option.label)} onChange={() => onChange({ ...value, selected: multiple ? value.selected.includes(option.label) ? value.selected.filter(label => label !== option.label) : [...value.selected, option.label] : [option.label] })} />{option.text ? <><strong>{option.label}</strong><span>{option.text}</span></> : option.label}</label>)}</div></fieldset>
  }
  if (item.question_kind === 'fill_blank' && item.answer_fields?.length) return <fieldset className="practice-blanks" disabled={disabled}><legend>逐空填写</legend>{item.answer_fields.map((field, index) => <div className="answer-field" key={field.id}><label htmlFor={`${id}-${index}`}>{field.label || `第 ${index + 1} 空`}</label><input id={`${id}-${index}`} type="text" inputMode="text" autoComplete="off" maxLength={PRACTICE_ANSWER_MAX} value={value.fields[field.id] ?? ''} placeholder="填写这一空" onChange={event => onChange({ ...value, fields: { ...value.fields, [field.id]: event.target.value } })} /></div>)}</fieldset>
  if (item.question_kind === 'solution' || item.answer_kind === 'text') return <div className="answer-field"><label htmlFor={id}>你的解答和过程</label><textarea id={id} rows={5} maxLength={PRACTICE_ANSWER_MAX} value={value.text} disabled={disabled} placeholder="写下思路、计算过程和答案；可以分行输入。" onChange={event => onChange({ ...value, text: event.target.value })} /><span className="muted small">可以用 / 表示分数、^ 表示乘方，例如 3/4、x^2。</span></div>
  return <div className="answer-field"><label htmlFor={id}>你的答案</label><input id={id} type="text" inputMode="text" autoComplete="off" maxLength={PRACTICE_ANSWER_MAX} value={value.text} disabled={disabled} placeholder="例如：-2 或 3/4" onChange={event => onChange({ ...value, text: event.target.value })} /></div>
}

export function restoredPracticeInput(item: PracticeItem): PracticeInputValue {
  const submission = item.assessment?.submission
  return { text: submission?.answer_text ?? '', selected: submission?.selected_options ?? [],
    fields: Object.fromEntries((submission?.answers ?? []).map(answer => [answer.field_id, answer.answer_text])) }
}

export function PracticeAssessmentNotice({ assessment }: { assessment: PracticeAssessment }) {
  const pending = assessmentPending(assessment)
  const message = pending ? '正在核对解法' : assessment.status === 'completed' ? '核验已完成，正在读取结果' : assessment.status === 'failed' ? '这次还没完成核验' : '这次核验已停止'
  return <div className={`practice-assessment-notice ${assessment.status}`} role="status">
    {pending && <LoaderCircle className="spin" size={18} />}
    <div><strong>{message}</strong><p>{pending ? '你的作答已保存。刷新后会继续查看进度，不用再交一次。' : assessment.status === 'failed' ? `${assessment.feedback} 原答案已保留，尚未记成对错。` : assessment.status === 'superseded' ? '活动或题目记录已更新，这次核验不计入结果。' : '正在读取已保存的作答结果，不会重复核验。'}</p></div>
  </div>
}

export function PracticeItemCard({ item, index, active, hintsAllowed, hint, busy, submissionUnconfirmed = false, onAnswer, onHint }: { item: PracticeItem; index: number; active: boolean; hintsAllowed: boolean; hint?: string; busy: boolean; submissionUnconfirmed?: boolean; onAnswer: (answer: PracticeSubmission) => Promise<boolean>; onHint: () => void }) {
  const [answer, setAnswer] = useState<PracticeInputValue>(() => restoredPracticeInput(item))
  const restoredId = useRef(item.assessment?.assessment_id)
  useEffect(() => {
    if (item.assessment?.submission && restoredId.current !== item.assessment.assessment_id) {
      setAnswer(restoredPracticeInput(item)); restoredId.current = item.assessment.assessment_id
    }
  }, [item.assessment])
  const submission = buildPracticeSubmission(item, answer)
  const pending = assessmentPending(item.assessment)
  const assessmentBlocks = !!item.assessment && !item.response && (pending || item.assessment.status === 'completed' || item.assessment.status === 'superseded')
  const disabled = busy || !active || assessmentBlocks || submissionUnconfirmed
  const submit = async (event: FormEvent) => { event.preventDefault(); if (!disabled && practiceSubmissionReady(submission) && await onAnswer(submission)) setAnswer({ text: '', selected: [], fields: {} }) }
  const feedback = item.response ? answerFeedback[item.response.result] : null
  return <article className="practice-item" aria-labelledby={`${item.id}-stem`}>
    <div className="practice-item-meta"><span className="eyebrow">第 {index + 1} 题</span>{item.verification && <span className="practice-source">{item.verification.source === 'ai' ? 'AI 出题 · 已独立核验' : '老师已核验'}</span>}</div>
    <p id={`${item.id}-stem`} className="practice-stem">{item.stem_text}</p>
    {item.response && !!item.options?.length && <ul className="practice-option-review">{item.options.map(option => <li key={option.label}><strong>{option.label}</strong><span>{option.text}</span></li>)}</ul>}
    {!item.response && item.assessment && <PracticeAssessmentNotice assessment={item.assessment} />}
    {item.response ? <div className="practice-feedback" aria-live="polite">
      <div className="answer-block"><span>你的答案</span><p>{item.response.answer_text}</p></div>
      {feedback && <div className={`result-callout ${feedback.tone}`}><span className="status-dot" /><div><strong>{feedback.title}</strong><p>{item.response.feedback || feedback.text}</p></div></div>}
      {item.solution_text && <div className="hint-note"><span className="eyebrow">解析</span><p>{item.solution_text}</p></div>}
    </div> : active || item.assessment ? <form className="practice-form" onSubmit={event => void submit(event)}>
      <AnswerInput item={item} value={answer} disabled={disabled} onChange={setAnswer} />
      {[...submission.answer_text].length > PRACTICE_ANSWER_MAX && <p className="inline-error" role="alert">这道题的答案请控制在 {PRACTICE_ANSWER_MAX} 字内。</p>}
      {active && <div className="hint-actions"><button type="submit" className="button primary" disabled={disabled || !practiceSubmissionReady(submission)}>{busy || pending ? <LoaderCircle className="spin" size={16} /> : item.assessment?.retryable ? <RefreshCw size={16} /> : <Check size={16} />} {pending ? '正在核对解法' : submissionUnconfirmed ? '先刷新提交状态' : busy ? '正在提交' : item.assessment?.retryable ? '重试核验' : '交上这道题'}</button>{hintsAllowed && item.has_hint && !hint && <button type="button" className="button quiet" disabled={disabled} onClick={onHint}><Lightbulb size={16} /> 给我一点提示</button>}</div>}
      {hint && <div className="hint-note" aria-live="polite"><span className="eyebrow">提示</span><p>{hint}</p></div>}
    </form> : <p className="muted small">这道题没有做，不用补。</p>}
  </article>
}

export function WrapUp({ session, now, onClose }: { session: PracticeSession; now: number; onClose: () => void }) {
  const status = effectiveStatus(session, now)
  if (status === 'active') return null
  const copy = status === 'completed' && !session.items.length ? readingDone : wrapUps[status]
  return <div className="wrap-up" role="status"><span className="ready-symbol"><Check size={24} /></span><h2>{copy.title}</h2><p>{copy.text}</p><button className="button primary" onClick={onClose}><ArrowLeft size={17} /> 回到作业本</button></div>
}

/** One voluntary session. 结束 is always one tap away; leaving early never creates debt. */
export function PracticeRunner({ workspace, initial, notice, onClose }: { workspace: Workspace; initial: PracticeSession; notice?: string; onClose: () => void }) {
  const [session, setSession] = useState(initial)
  const [now, setNow] = useState(() => Date.now())
  const [hints, setHints] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [exiting, setExiting] = useState(false)
  const [unconfirmed, setUnconfirmed] = useState<string[]>([])
  const [recovering, setRecovering] = useState(false)
  const busyRef = useRef(false), readVersion = useRef(0), mounted = useRef(false)
  const { client } = workspace
  const sessionId = initial.id
  const reload = useCallback(async (): Promise<PracticeSession | null> => {
    const version = ++readVersion.current
    try {
      const next = await client.practiceSession(sessionId)
      if (!mounted.current || version !== readVersion.current) return null
      setSession(next); setUnconfirmed([])
      return next
    } catch (e) {
      if (mounted.current && version === readVersion.current) {
        setError(SESSION_READ_ERROR)
        workspace.report(e)
      }
      return null
    }
  }, [client, sessionId])
  useEffect(() => { mounted.current = true; void reload(); return () => { mounted.current = false; readVersion.current++ } }, [reload])
  useEffect(() => { const timer = setInterval(() => setNow(Date.now()), 15_000); return () => clearInterval(timer) }, [])
  const pendingAssessments = session.items.filter(item => !item.response && assessmentPending(item.assessment)).map(item => item.assessment!)
  const pendingKey = pendingAssessments.map(assessment => `${assessment.item_id}:${assessment.assessment_id}`).join('|')
  useEffect(() => {
    if (!pendingKey) return
    let cancelled = false
    let timer: ReturnType<typeof setTimeout>
    const poll = async () => {
      const version = readVersion.current
      const updates = await Promise.allSettled(pendingAssessments.map(assessment => client.practiceAssessment(assessment.assessment_id, sessionId, assessment.item_id)))
      if (cancelled || !mounted.current) return
      if (version !== readVersion.current) { timer = setTimeout(() => void poll(), 3000); return }
      if (updates.every(update => update.status === 'fulfilled')) setError(old => old === SESSION_READ_ERROR || old === ASSESSMENT_READ_ERROR ? '' : old)
      let terminal = false
      for (const update of updates) {
        if (update.status === 'fulfilled') {
          setSession(old => applyPracticeAssessment(old, update.value))
          if (!assessmentPending(update.value)) terminal = true
        } else setError(ASSESSMENT_READ_ERROR)
      }
      if (terminal) await reload()
      if (!cancelled) timer = setTimeout(() => void poll(), 3000)
    }
    timer = setTimeout(() => void poll(), 1200)
    return () => { cancelled = true; clearTimeout(timer) }
  }, [client, sessionId, pendingKey, reload])
  const status = effectiveStatus(session, now), active = status === 'active'
  const refresh = async () => {
    if (recovering || busyRef.current) return
    setRecovering(true)
    const next = await reload()
    if (mounted.current) { if (next) setError(''); setRecovering(false) }
  }
  const act = async (task: () => Promise<void | boolean>) => {
    if (busyRef.current) return false
    busyRef.current = true; readVersion.current++
    setBusy(true); setError('')
    try { return (await task()) !== false }
    catch (e) {
      if (!mounted.current) return false
      if (e instanceof StudentError && e.status === 409 && ['revision_conflict', 'assessment_pending'].includes(e.code)) {
        setError(e.code === 'assessment_pending' ? '上次作答正在核验，正在恢复已提交的记录。' : '这道题已经交过了，或者活动已经结束。已为你刷新。')
        await reload()
      } else { setError(practiceMessage(e, safeError(e).message)); workspace.report(e) }
      return false
    } finally { busyRef.current = false; if (mounted.current) { setBusy(false); setNow(Date.now()) } }
  }
  const answer = (item: PracticeItem, submission: PracticeSubmission) => act(async () => {
    let result
    try { result = await client.answerPractice(sessionId, item.id, submission) }
    catch (e) {
      if (mounted.current) {
        // A lost receipt may still represent a saved job. Recover with GET before allowing another POST.
        setUnconfirmed(old => [...new Set([...old, item.id])])
        const recovered = (await reload())?.items.find(next => next.id === item.id)
        if (recovered?.response || assessmentPending(recovered?.assessment) ||
          (recovered?.assessment && recovered.assessment.assessment_id !== item.assessment?.assessment_id)) return false
      }
      throw e
    }
    if (!mounted.current) return false
    if (result.result !== 'pending' && result.retryable) { setError(result.feedback || '这次还没有完成核验，答案已保留，可以稍后再提交。'); return false }
    setSession(old => applyPracticeAnswer(old, sessionId, item.id, result, submission, !!hints[item.id]))
    return result.result !== 'pending'
  })
  const hint = (item: PracticeItem) => void act(async () => { const value = await client.practiceHint(sessionId, item.id); if (mounted.current) setHints(old => ({ ...old, [item.id]: value.hint_text })) })
  const exit = (reason: ExitReason) => void act(async () => { const next = await client.exitPractice(sessionId, reason); if (mounted.current) { setSession(next); setExiting(false) } })
  const quiz = session.purpose === 'preview' && session.items.length > 0
  return <section className="practice-page" aria-labelledby="practice-title">
    <div className="page-title-row"><div><span className="eyebrow">选做活动 · 随时可以结束</span><h1 id="practice-title">{session.purpose === 'variant' ? '举一反三' : quiz ? '预习 + 小测' : '下一课预习'}</h1><p>{session.notice}</p></div></div>
    {notice && <p className="message success" role="status">{notice}</p>}
    {error && <div className="inline-error" role="alert"><p>{error}</p><button className="text-button" disabled={busy || recovering} onClick={() => void refresh()}><RefreshCw size={15} /> 刷新核验状态</button></div>}
    <WrapUp session={session} now={now} onClose={onClose} />
    {session.targets && <PreviewReading targets={session.targets} />}
    {quiz && <p className="muted small practice-note">小测最多 {session.items.length} 题，不提供提示，可以随时结束。</p>}
    <div className="practice-items">{session.items.map((item, index) => <PracticeItemCard key={item.id} item={item} index={index} active={active} hintsAllowed={session.hints_allowed} hint={hints[item.id]} busy={busy || recovering} submissionUnconfirmed={unconfirmed.includes(item.id)} onAnswer={text => answer(item, text)} onHint={() => hint(item)} />)}</div>
    {active && session.purpose === 'preview' && !session.items.length && <button className="button secondary full" disabled={busy} onClick={() => exit('done')}><Check size={17} /> 我看完了</button>}
    <div className="practice-bar">
      {active ? exiting ? <div className="exit-choices" role="group" aria-label="怎么结束都可以"><span>怎么结束都可以：</span>{exitChoices.map(choice => <button key={choice.reason} className="button secondary" disabled={busy} onClick={() => exit(choice.reason)}>{choice.label}</button>)}<button className="button quiet" disabled={busy} onClick={() => setExiting(false)}>继续做</button></div>
        : <><span className="practice-time" aria-live="polite"><Clock3 size={16} /> 还可以用 {remainingLabel(session.budget_ends_at, now)}</span><button className="button secondary practice-exit" disabled={busy} onClick={() => setExiting(true)}>{busy ? <LoaderCircle className="spin" size={18} /> : <LogOut size={18} />} 结束</button></>
        : <button className="button primary practice-exit" onClick={onClose}><ArrowLeft size={18} /> 回到作业本</button>}
    </div>
  </section>
}
