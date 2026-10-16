import { useEffect, useState } from 'react'
import { Check, HandHelping } from 'lucide-react'
import type { AttemptRead, Hint } from '../api/contracts.generated'
import type { AssistanceInput, AttemptCondition } from '../api/learningTypes'
import { assistanceChoices, assistanceLabels, dateTime, helpSeen } from '../learning'
import type { LearningContext } from './LearningCommon'

/** Records how much help an attempt had; this decides whether it can count as independent evidence. */
export default function AttemptConditionSection({ ctx, attempt, label, hints, recorded, onRecorded, locked }: {
  ctx: LearningContext; attempt: AttemptRead; label: string; hints: Hint[]
  recorded: AttemptCondition | undefined; onRecorded: (condition: AttemptCondition) => void; locked: string | null
}) {
  const seen = helpSeen(attempt, hints)
  const [level, setLevel] = useState<AssistanceInput | ''>('')
  const [note, setNote] = useState('')
  const isCorrection = attempt.record.parent_attempt_id !== null
  const [loaded, setLoaded] = useState(!!recorded)
  const disabled = ctx.busy || !!locked || !loaded

  useEffect(() => {
    if (recorded) return
    const controller = new AbortController()
    ctx.learning.condition(attempt.record.id, controller.signal)
      .then(read => { if (read.attempt_id === attempt.record.id && read.current) onRecorded(read.current); setLoaded(true) })
      .catch(() => { if (!controller.signal.aborted) setLoaded(true) })
    return () => controller.abort()
  }, [attempt.record.id])

  async function submit() {
    if (!level || (level === 'independent' && seen)) return
    const saved = await ctx.act(() => ctx.learning.recordCondition(attempt.record.id, { assistance_level: level, expected_condition_id: recorded?.id ?? null, note: note.trim() || null }),
      `已记录「${label}」的帮助情况：${assistanceLabels[level]}。`)
    if (saved) { onRecorded(saved); setLevel(''); setNote('') }
  }

  return <section className="panel-section review-form" aria-label="作答帮助情况">
    <div className="section-heading"><span><HandHelping size={17} />帮助情况</span><small>{label}</small></div>
    <p className="subtle">记录孩子做这次作答时得到了多少帮助。只有经老师核实的独立首次作答，才算独立完成的证据；订正永远不算独立。</p>
    {recorded ? <p className="preview-note">当前记录：{assistanceLabels[recorded.assistance_level]} · {dateTime(recorded.created_at)}；再次提交会替换这条记录。</p>
      : <p className="subtle">{loaded ? '这次作答还没有记录帮助情况。' : '正在读取已有记录…'}</p>}
    {locked && <div className="preview-note">{locked}</div>}
    <fieldset className="choice-list" disabled={disabled}>
      <legend>这次作答的帮助情况</legend>
      {assistanceChoices.map(choice => {
        const blocked = choice === 'independent' && (seen || isCorrection)
        return <label key={choice} className="check"><input type="radio" name={`assistance-${attempt.record.id}`} value={choice} checked={level === choice} disabled={blocked} onChange={() => setLevel(choice)} />
          <span>{assistanceLabels[choice]}{choice === 'independent' && <small className="choice-note">只有老师亲眼核实独立完成才勾选独立{seen ? '；系统已记录这次看过提示或答案，不能选' : isCorrection ? '；订正不算独立完成' : ''}</small>}</span></label>
      })}
    </fieldset>
    <label>备注（可选）<textarea value={note} maxLength={500} disabled={disabled} placeholder="例如：老师在旁看着独立写完，未提示" onChange={event => setNote(event.target.value)} /></label>
    <button className="button primary full" disabled={disabled || !level} onClick={() => void submit()}><Check size={15} />记录帮助情况</button>
  </section>
}
