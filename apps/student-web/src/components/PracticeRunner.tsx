import { useCallback, useEffect, useId, useState, type FormEvent } from 'react'
import { ArrowLeft, Check, Clock3, Lightbulb, LoaderCircle, LogOut } from 'lucide-react'
import { PRACTICE_ANSWER_MAX, safeError, StudentError, type ExitReason, type PracticeItem, type PracticeSession, type PreviewTarget } from '../core'
import type { Workspace } from '../useStudentWorkspace'
import { answerFeedback, choiceLetters, effectiveStatus, exitChoices, practiceMessage, readingDone, remainingLabel, statusLabels, wrapUps } from '../learning'

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

function AnswerInput({ item, value, disabled, onChange }: { item: PracticeItem; value: string; disabled: boolean; onChange: (value: string) => void }) {
  const id = useId()
  if (item.answer_kind === 'choice') return <fieldset className="choice-field" disabled={disabled}><legend>选一个答案</legend><div className="choice-options">{choiceLetters.map(letter => <label key={letter} className={value === letter ? 'selected' : ''}><input type="radio" name={`${item.id}-choice`} value={letter} checked={value === letter} onChange={() => onChange(letter)} />{letter}</label>)}</div></fieldset>
  return <div className="answer-field"><label htmlFor={id}>你的答案</label><input id={id} type="text" inputMode={item.answer_kind === 'number' ? 'decimal' : 'text'} autoComplete="off" maxLength={PRACTICE_ANSWER_MAX} value={value} disabled={disabled} placeholder={item.answer_kind === 'number' ? '例如：-2 或 3/4' : '写下你的答案'} onChange={event => onChange(event.target.value)} /></div>
}

export function PracticeItemCard({ item, index, active, hintsAllowed, hint, busy, onAnswer, onHint }: { item: PracticeItem; index: number; active: boolean; hintsAllowed: boolean; hint?: string; busy: boolean; onAnswer: (answer: string) => Promise<boolean>; onHint: () => void }) {
  const [answer, setAnswer] = useState('')
  const submit = async (event: FormEvent) => { event.preventDefault(); if (await onAnswer(answer)) setAnswer('') }
  const feedback = item.response ? answerFeedback[item.response.result] : null
  return <article className="practice-item" aria-labelledby={`${item.id}-stem`}>
    <span className="eyebrow">第 {index + 1} 题</span>
    <p id={`${item.id}-stem`} className="practice-stem">{item.stem_text}</p>
    {item.response ? <div className="practice-feedback" aria-live="polite">
      <div className="answer-block"><span>你的答案</span><p>{item.response.answer_text}</p></div>
      {feedback && <div className={`result-callout ${feedback.tone}`}><span className="status-dot" /><div><strong>{feedback.title}</strong><p>{feedback.text}</p></div></div>}
      {item.solution_text && <div className="hint-note"><span className="eyebrow">解析</span><p>{item.solution_text}</p></div>}
    </div> : active ? <form className="practice-form" onSubmit={event => void submit(event)}>
      <AnswerInput item={item} value={answer} disabled={busy} onChange={setAnswer} />
      <div className="hint-actions"><button type="submit" className="button primary" disabled={busy || !answer.trim()}><Check size={16} /> 交上这道题</button>{hintsAllowed && item.has_hint && !hint && <button type="button" className="button quiet" disabled={busy} onClick={onHint}><Lightbulb size={16} /> 给我一点提示</button>}</div>
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
  const { client } = workspace
  const reload = useCallback(async () => { try { setSession(await client.practiceSession(initial.id)) } catch (e) { workspace.report(e) } }, [client, initial.id])
  useEffect(() => { void reload() }, [reload])
  useEffect(() => { const timer = setInterval(() => setNow(Date.now()), 15_000); return () => clearInterval(timer) }, [])
  const status = effectiveStatus(session, now), active = status === 'active'
  const act = async (task: () => Promise<void>) => {
    if (busy) return false
    setBusy(true); setError('')
    try { await task(); return true }
    catch (e) {
      if (e instanceof StudentError && e.status === 409 && e.code === 'revision_conflict') { setError('这道题已经交过了，或者活动已经结束。已为你刷新。'); await reload() }
      else { setError(practiceMessage(e, safeError(e).message)); workspace.report(e) }
      return false
    } finally { setBusy(false); setNow(Date.now()) }
  }
  const answer = (item: PracticeItem, text: string) => act(async () => {
    const result = await client.answerPractice(session.id, item.id, text)
    setSession(old => ({ ...old, status: result.session_status, items: old.items.map(i => i.id === item.id ? { ...i, response: { answer_text: text.trim(), result: result.result, hint_used: !!hints[i.id] }, solution_text: result.solution_text } : i) }))
  })
  const hint = (item: PracticeItem) => void act(async () => { const value = await client.practiceHint(session.id, item.id); setHints(old => ({ ...old, [item.id]: value.hint_text })) })
  const exit = (reason: ExitReason) => void act(async () => { setSession(await client.exitPractice(session.id, reason)); setExiting(false) })
  const quiz = session.purpose === 'preview' && session.items.length > 0
  return <section className="practice-page" aria-labelledby="practice-title">
    <div className="page-title-row"><div><span className="eyebrow">选做活动 · 随时可以结束</span><h1 id="practice-title">{session.purpose === 'variant' ? '举一反三' : quiz ? '预习 + 小测' : '下一课预习'}</h1><p>{session.notice}</p></div></div>
    {notice && <p className="message success" role="status">{notice}</p>}
    {error && <div className="inline-error" role="alert"><p>{error}</p></div>}
    <WrapUp session={session} now={now} onClose={onClose} />
    {session.targets && <PreviewReading targets={session.targets} />}
    {quiz && <p className="muted small practice-note">小测最多 {session.items.length} 题，不提供提示，可以随时结束。</p>}
    <div className="practice-items">{session.items.map((item, index) => <PracticeItemCard key={item.id} item={item} index={index} active={active} hintsAllowed={session.hints_allowed} hint={hints[item.id]} busy={busy} onAnswer={text => answer(item, text)} onHint={() => hint(item)} />)}</div>
    {active && session.purpose === 'preview' && !session.items.length && <button className="button secondary full" disabled={busy} onClick={() => exit('done')}><Check size={17} /> 我看完了</button>}
    <div className="practice-bar">
      {active ? exiting ? <div className="exit-choices" role="group" aria-label="怎么结束都可以"><span>怎么结束都可以：</span>{exitChoices.map(choice => <button key={choice.reason} className="button secondary" disabled={busy} onClick={() => exit(choice.reason)}>{choice.label}</button>)}<button className="button quiet" disabled={busy} onClick={() => setExiting(false)}>继续做</button></div>
        : <><span className="practice-time" aria-live="polite"><Clock3 size={16} /> 还可以用 {remainingLabel(session.budget_ends_at, now)}</span><button className="button secondary practice-exit" disabled={busy} onClick={() => setExiting(true)}>{busy ? <LoaderCircle className="spin" size={18} /> : <LogOut size={18} />} 结束</button></>
        : <button className="button primary practice-exit" onClick={onClose}><ArrowLeft size={18} /> 回到作业本</button>}
    </div>
  </section>
}
