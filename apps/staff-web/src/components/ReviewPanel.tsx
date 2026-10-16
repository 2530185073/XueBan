import { useState } from 'react'
import { Check, ChevronDown, CircleHelp, FileCheck2, History, MessageCircle, ShieldCheck } from 'lucide-react'
import type { IdentityContext, SubmitReview, Verdict } from '../api/contracts.generated'
import type { CaseWorkspace } from '../workspace'

export const verdictLabel: Record<Verdict, string> = { correct: '正确', incorrect: '需要订正', partially_correct: '部分正确', unanswered: '未作答', unable_to_judge: '无法判断' }
const helpLabels = { unknown: '帮助条件未知', none_reported: '未报告帮助', hint_used: '使用过提示', staff_help: '老师帮助', answer_shown: '看过答案' }
const at = (value: string) => new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit' }).format(new Date(value))

export default function ReviewPanel({ workspace, identity, sample, busy, onReview, onComplete }: {
  workspace: CaseWorkspace; identity: IdentityContext | null; sample: boolean; busy: boolean
  onReview: (payload: SubmitReview) => void
  onComplete: (outcome: 'resolved' | 'unresolved', note: string) => void
}) {
  const [action, setAction] = useState<SubmitReview['action']>(workspace.decision ? 'confirm' : 'manual_initial')
  const [verdict, setVerdict] = useState<Verdict | ''>('')
  const [reason, setReason] = useState('')
  const [basis, setBasis] = useState('')
  const [helpNote, setHelpNote] = useState('')
  const decision = workspace.decision?.record
  const currentAttempt = workspace.attempts.find(a => a.record.id === workspace.correction.current_attempt_id)
  const claimed = workspace.help.status === 'claimed' && workspace.help.claimed_by === identity?.actor_id
  const fresh = workspace.question.currently_effective && workspace.question.record.lifecycle === 'active' &&
    workspace.correction.lifecycle === 'active' && currentAttempt?.currently_effective && currentAttempt.record.recognition_status === 'ready' &&
    (!decision || (workspace.decision?.currently_effective && !['stale', 'retracted'].includes(decision.validity) &&
      decision.input_versions.question_revision === workspace.question.current_revision &&
      decision.input_versions.attempt_id === currentAttempt.record.id &&
      decision.input_versions.attempt_recognition_revision === currentAttempt.record.recognition_revision))
  const finalVerdict = action === 'confirm' ? decision?.verdict : ['retract', 'leave_unresolved'].includes(action) ? null : verdict
  const validVerdict = ['retract', 'leave_unresolved'].includes(action) || (finalVerdict && finalVerdict !== 'unable_to_judge')
  const enabled = !sample && claimed && fresh && !busy && reason.trim() && basis.trim() && validVerdict

  function submit() {
    if (!enabled || !currentAttempt) return
    onReview({
      expected_revision: workspace.correction.revision,
      target_decision_id: action === 'manual_initial' ? null : decision!.id,
      target_decision_revision: action === 'manual_initial' ? null : decision!.revision,
      action, verdict: finalVerdict as Verdict | null, reason: reason.trim(),
      basis: [{ kind: 'staff_observation', summary: basis.trim(), source_ref: null, regions: workspace.question.record.regions }],
      attempt_id: currentAttempt.record.id, question_revision: workspace.question.current_revision,
      attempt_recognition_revision: currentAttempt.record.recognition_revision,
    })
  }

  return <aside className="review-panel" aria-label="候选判断与处理记录">
    <section className="panel-section candidate-section">
      <div className="section-heading"><span><ShieldCheck size={17} /> 当前判断</span><span className="tag amber">{decision?.validity === 'confirmed' ? '老师已确认' : '等待核实'}</span></div>
      {decision ? <>
        <h2>{verdictLabel[decision.verdict]}</h2>
        <p className="subtle">{decision.origin === 'staff' ? '现场核实记录' : 'AI / 规则候选'} · 版本 {decision.revision}{sample && ' · 合成内容'}</p>
        <div className="evidence-note">{decision.basis.map((item, index) => <p key={index}>{item.summary}</p>)}</div>
        {!workspace.decision?.currently_effective && <p className="warning-text">此判断已经失效，不能用于新的复核。</p>}
      </> : <p className="subtle">尚无可用判断。请对照原图；接口接通后可进行有依据的人工初次核实。</p>}
    </section>

    <section className="panel-section">
      <div className="section-heading"><span><History size={17} /> 作答与帮助</span><small>保留每一次过程</small></div>
      <ol className="timeline">
        {[...workspace.attempts].sort((a, b) => Date.parse(a.record.occurred_at) - Date.parse(b.record.occurred_at)).map(attempt => <li key={attempt.record.id}>
          <span className="timeline-dot" /><div><div className="timeline-title">{attempt.record.parent_attempt_id === null ? '原始作答' : '订正作答'}<time>{at(attempt.record.occurred_at)}</time></div>
          <p className="answer-line">{attempt.record.answer_text ?? '尚无可用识别文本'}</p><small>{helpLabels[attempt.record.help_condition]}</small></div>
        </li>)}
        {workspace.hints.map(hint => <li key={hint.id}><span className="timeline-dot hint" /><div>
          <div className="timeline-title">{hint.first_shown_at ? '孩子已看到提示' : '提示已生成，未记录展示'}{hint.first_shown_at && <time>{at(hint.first_shown_at)}</time>}</div>
          <p>{hint.content}</p><small>{hint.source_is_provisional ? '基于候选判断' : '基于已核实判断'}{hint.validity !== 'active' && ' · 来源已失效'}{hint.exposes_answer && ' · 包含答案'}</small>
        </div></li>)}
        <li><span className="timeline-dot question" /><div><div className="timeline-title">孩子请求帮助<time>{at(workspace.help.created_at)}</time></div><p>{workspace.help.note}</p></div></li>
      </ol>
    </section>

    <section className="panel-section review-form">
      <div className="section-heading"><span><FileCheck2 size={17} /> 现场复核</span></div>
      <p className="subtle">先核对原图，再记录依据。结束帮助与确认答对分别记录。</p>
      {sample && <div className="preview-note">合成预览 · 操作不会提交或保存</div>}
      {!sample && !claimed && <div className="preview-note">请先接手此求助，才可提交处理记录。</div>}
      {!fresh && <div className="preview-note">题目或作答版本已变化，请刷新后核实。</div>}
      <label>处理方式<div className="select-wrap"><select value={action} disabled={busy || sample} onChange={event => setAction(event.target.value as SubmitReview['action'])}>
        {decision ? <><option value="confirm">确认当前候选</option><option value="replace">更正当前判断</option><option value="leave_unresolved">保留待核实</option><option value="retract">撤销当前判断</option></> : <option value="manual_initial">人工初次核实</option>}
      </select><ChevronDown size={14} /></div></label>
      {['replace', 'manual_initial'].includes(action) && <label>核实结果<div className="select-wrap"><select value={verdict} disabled={busy || sample} onChange={event => setVerdict(event.target.value as Verdict)}><option value="">请选择结果</option><option value="correct">正确</option><option value="incorrect">需要订正</option><option value="partially_correct">部分正确</option><option value="unanswered">未作答</option></select><ChevronDown size={14} /></div></label>}
      <label>处理说明<textarea placeholder="说明为什么确认、更正或暂时无法判断…" value={reason} maxLength={2000} disabled={busy || sample} onChange={event => setReason(event.target.value)} /></label>
      <label>原图核实依据<textarea placeholder="写下你在原图中核实的步骤或条件…" value={basis} maxLength={2000} disabled={busy || sample} onChange={event => setBasis(event.target.value)} /></label>
      <button className="button primary full" disabled={!enabled} onClick={submit}><Check size={16} />{busy ? '等待服务端确认…' : '提交复核记录'}</button>
      <details className="help-completion"><summary><MessageCircle size={15} />记录本次现场帮助<ChevronDown size={14} /></summary>
        <label>帮助记录<textarea placeholder="记录这次如何帮助孩子，以及仍未解决的部分…" value={helpNote} maxLength={1500} disabled={sample || busy} onChange={event => setHelpNote(event.target.value)} /></label>
        <div className="button-row"><button className="button" disabled={sample || busy || !claimed || !helpNote.trim()} onClick={() => onComplete('resolved', helpNote.trim())}>帮助已结束</button><button className="button" disabled={sample || busy || !claimed || !helpNote.trim()} onClick={() => onComplete('unresolved', helpNote.trim())}>仍需帮助</button></div>
        <small><CircleHelp size={12} /> 此操作不会把题目标记为答对。</small>
      </details>
    </section>
  </aside>
}
