import { useCallback, useEffect, useId, useState } from 'react'
import { BookOpenText, Coffee, LoaderCircle, Sprout } from 'lucide-react'
import type { OptionalActivities, PracticeSession, StartPractice, VariantOffer } from '../core/learning'
import { StudentError } from '../core/errors'
import type { Workspace } from '../useStudentWorkspace'
import { activityOffers, clampCount, isActiveConflict, practiceMessage, RESUME_NOTICE, variantDefault, variantMax, type ActivityOffers } from '../learning'

type Choice = { kind: 'variant'; offer: VariantOffer; count: number } | { kind: 'preview'; withQuiz: boolean }
const requestOf = (choice: Choice): StartPractice => choice.kind === 'variant' ? { purpose: 'variant', willing: true, knowledge_id: choice.offer.knowledge_id, count: choice.count } : { purpose: 'preview', willing: true, with_quiz: choice.withQuiz }
const summaryOf = (choice: Choice) => choice.kind === 'variant' ? `举一反三：「${choice.offer.name}」${choice.count} 道题，可以看提示。` : choice.withQuiz ? '下一课预习，再做一个最多 3 题的小测（小测不提供提示）。' : '下一课预习，只读一读，不做题。'

export function ActivityChooser({ offers, busy, onStart }: { offers: ActivityOffers; busy: boolean; onStart: (request: StartPractice) => void }) {
  const [choice, setChoice] = useState<Choice | null>(null)
  const countId = useId()
  return <section className="optional-card" aria-labelledby={`${countId}-title`}>
    <div className="optional-head"><Coffee size={22} strokeWidth={1.5} /><div><h2 id={`${countId}-title`}>学校任务完成啦</h2><p>可以休息，或者就此结束今天的学习。如果还想练一练，下面这些都是选做的，做不做都可以。</p></div></div>
    {choice ? <div className="optional-confirm" role="group" aria-label="确认是否开始">
      <p><strong>{summaryOf(choice)}</strong></p>
      {choice.kind === 'variant' && <div className="count-field"><span id={`${countId}-count`}>做几道</span><div className="segmented" role="group" aria-labelledby={`${countId}-count`}>{Array.from({ length: variantMax(choice.offer) }, (_, index) => index + 1).map(n => <button key={n} type="button" aria-pressed={choice.count === n} className={choice.count === n ? 'active' : ''} disabled={busy} onClick={() => setChoice({ ...choice, count: clampCount(n, choice.offer) })}>{n} 道</button>)}</div></div>}
      <p className="muted small">大约有 {offers.remainingMinutes} 分钟。中途随时可以结束，不会记欠账。</p>
      <div className="optional-actions"><button className="button primary" disabled={busy} onClick={() => onStart(requestOf(choice))}>{busy ? <LoaderCircle className="spin" size={17} /> : <Sprout size={17} />} 我愿意试试</button><button className="button quiet" disabled={busy} onClick={() => setChoice(null)}>先不了</button></div>
    </div> : <div className="optional-options">
      {offers.variants.length > 0 && <div><h3>举一反三</h3><p className="muted small">换一道类似的题，再想一次。</p><div className="optional-buttons">{offers.variants.map(offer => <button key={offer.knowledge_id} className="button secondary" disabled={busy} onClick={() => setChoice({ kind: 'variant', offer, count: variantDefault(offer) })}>{offer.name}</button>)}</div></div>}
      {offers.preview && <div><h3>下一课预习</h3><p className="muted small">{offers.preview.names.join('、')}</p><div className="optional-buttons"><button className="button secondary" disabled={busy} onClick={() => setChoice({ kind: 'preview', withQuiz: false })}><BookOpenText size={16} /> 只读一读</button>{offers.preview.quiz_items_available > 0 && <button className="button secondary" disabled={busy} onClick={() => setChoice({ kind: 'preview', withQuiz: true })}>预习 + 小测</button>}</div></div>}
    </div>}
    <p className="muted small optional-notice">{offers.notice}</p>
  </section>
}

/** Shown only when the server opens today's gate; otherwise renders nothing at all. */
export function OptionalActivityCard({ workspace, onStarted }: { workspace: Workspace; onStarted: (session: PracticeSession, notice?: string) => void }) {
  const [activities, setActivities] = useState<OptionalActivities | null>(null)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const { client, studentId, recordVersion } = workspace
  const load = useCallback(() => client.optionalActivities().then(setActivities, (e: unknown) => { setActivities(null); workspace.report(e) }), [client])
  useEffect(() => { setActivities(null); setMessage(''); void load() }, [load, studentId, recordVersion])
  const start = async (request: StartPractice) => {
    if (busy) return
    setBusy(true); setMessage('')
    try { onStarted(await client.startPractice(request)) }
    catch (e) {
      // An active session also answers an unconfirmed earlier start; resume it instead of starting another.
      const resumable = isActiveConflict(e) || e instanceof StudentError && e.status === 0
      const active = resumable ? await client.activePractice().catch(() => null) : null
      if (active) onStarted(active, RESUME_NOTICE)
      else { setMessage(isActiveConflict(e) ? '刚才的活动已经结束了，想做的话可以重新选。' : practiceMessage(e, '这次没有开始。休息一下也很好。')); workspace.report(e); await load() }
    } finally { setBusy(false) }
  }
  const offers = activityOffers(activities)
  return <>
    {message && <p className="optional-message" role="status">{message}</p>}
    {offers && <ActivityChooser offers={offers} busy={busy} onStart={request => void start(request)} />}
  </>
}
