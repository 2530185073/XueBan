import { useState } from 'react'
import { Bot, Camera, Check, ChevronDown, CircleHelp, FileCheck2, History, Lightbulb, LoaderCircle, MessageCircle, RefreshCcw, ShieldCheck, UserCheck } from 'lucide-react'
import type { Basis, HelpRequest, Hint, SubmitReview, Verdict } from '../api/contracts.generated'
import { helpStatusLabels, kindLabels } from '../queue'
import { attemptChain, buildReview, currentAttempt, reviewBlock, reviewIssue, type CaseWorkspace, type ReviewForm } from '../workspace'

export const verdictLabel: Record<Verdict, string> = { correct: '正确', incorrect: '需要订正', partially_correct: '部分正确', unanswered: '未作答', unable_to_judge: '无法判断' }
const validityLabel = { candidate: 'AI 候选 · 未经老师确认', needs_review: 'AI 无法判断 · 需要老师核实', confirmed: '老师已确认', retracted: '已撤销', stale: '已失效' }
const originLabel = { model: 'AI 模型', rule: '规则校验', staff: '老师' }
const basisLabel: Record<Basis['kind'], string> = { calculation: '计算核对', equivalence: '等价判断', reviewed_reference: '已审参考', model_hypothesis: 'AI 推测', staff_observation: '老师观察' }
const helpLabels = { unknown: '帮助情况未知', none_reported: '未报告帮助', hint_used: '看过提示', staff_help: '老师帮助过', answer_shown: '看过答案' }
const recognitionLabels = { pending: '识别中', ready: '识别完成', needs_revision: '识别需修订', failed: '识别失败' }
const hintLevels: Record<Hint['level'], string> = { direction: '方向提示', key_step: '关键步骤', staff_handoff: '转交老师' }
const actionLabels: Record<SubmitReview['action'], string> = { confirm: '确认候选', replace: '更正判断', retract: '撤销判断', leave_unresolved: '保留待核实', manual_initial: '人工初次核实' }
const at = (value: string) => new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }).format(new Date(value))

export default function ReviewPanel({ ws, focus, actorId, block, sample, busy, attemptId, onAttempt, onClaim, onComplete, onReview, onStartCheck }: {
  ws: CaseWorkspace; focus: HelpRequest | null; actorId: string | undefined; block: string | null; sample: boolean; busy: boolean
  attemptId: string; onAttempt: (id: string) => void
  onClaim: (help: HelpRequest) => Promise<boolean>
  onComplete: (help: HelpRequest, outcome: 'resolved' | 'unresolved', note: string) => Promise<boolean>
  onReview: (payload: SubmitReview) => Promise<boolean>
  onStartCheck: () => Promise<boolean>
}) {
  const decision = ws.decision?.record ?? null
  const [form, setForm] = useState<ReviewForm>({ action: decision ? 'confirm' : 'manual_initial', verdict: '', reason: '', basis: '', regions: [] })
  const [helpNote, setHelpNote] = useState('')
  const chain = attemptChain(ws.attempts, ws.correction.current_attempt_id)
  const attempt = currentAttempt(ws)
  const stale = reviewBlock(ws)
  const locked = busy || !!block
  // The available actions follow the loaded decision, even if the form was filled before a reload.
  const action: SubmitReview['action'] = decision ? form.action === 'manual_initial' ? 'confirm' : form.action : 'manual_initial'
  const effective: ReviewForm = { ...form, action, regions: ws.question.record.regions }
  const issue = reviewIssue(ws, effective)
  const submittable = !locked && !stale && !issue
  const mine = focus?.status === 'claimed' && focus.claimed_by === actorId
  const canCheck = !locked && !decision && ws.correction.lifecycle === 'active' && attempt?.record.recognition_status === 'ready' && ws.question.currently_effective && ws.correction.progress !== 'rechecking'
  const set = (change: Partial<ReviewForm>) => setForm(old => ({ ...old, ...change }))

  async function submit() {
    if (!submittable) return
    if (await onReview(buildReview(ws, effective))) setForm({ action: 'confirm', verdict: '', reason: '', basis: '', regions: [] })
  }
  async function complete(outcome: 'resolved' | 'unresolved') {
    if (focus && await onComplete(focus, outcome, helpNote.trim())) setHelpNote('')
  }

  return <aside className="review-panel" aria-label="判断、过程与处理">
    <section className="panel-section candidate-section">
      <div className="section-heading"><span><ShieldCheck size={17} />当前判断</span>{decision && <span className={`validity validity-${decision.validity}`}>{decision.validity === 'confirmed' ? <UserCheck size={12} /> : <Bot size={12} />}{validityLabel[decision.validity]}</span>}</div>
      {decision ? <div className={`decision-card decision-${decision.validity === 'confirmed' ? 'confirmed' : 'candidate'}`}>
        <h2>{verdictLabel[decision.verdict]}</h2>
        <p className="subtle">来源：{originLabel[decision.origin]}{ws.check?.model_version && ` · ${ws.check.model_version}`} · 版本 {decision.revision}
          {decision.confirmed_at && <> · {decision.confirmed_by === actorId ? '你' : '老师'}于 {at(decision.confirmed_at)} 确认</>}</p>
        <ul className="basis-list">{decision.basis.map((item, i) => <li key={i}><span className={`tag ${item.kind === 'model_hypothesis' ? 'amber' : ''}`}>{basisLabel[item.kind]}</span><p>{item.summary}</p></li>)}</ul>
        {!ws.decision?.currently_effective && <p className="warning-text">此判断已经失效，不能用于新的复核。</p>}
      </div> : <p className="subtle">{ws.correction.progress === 'rechecking' ? '检查进行中，结果出来前请先对照原图。' : '当前没有有效判断（可能在题目或答案修订后失效）。可以请求重新检查，或直接人工初次核实。'}</p>}
      {!decision && <button className="button full" disabled={!canCheck} onClick={() => void onStartCheck()}><RefreshCcw size={15} />请求重新检查</button>}
    </section>

    <section className="panel-section">
      <div className="section-heading"><span><MessageCircle size={17} />求助与申请重查</span><small>{ws.helps.length} 条</small></div>
      {ws.helps.length === 0 && <p className="subtle">这道题没有孩子发起的求助。</p>}
      <ul className="help-list">{[...ws.helps].sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at)).map(help => <li key={help.id} className={help.id === focus?.id ? 'focused' : ''}>
        <div className="help-row"><span className={`tag ${help.kind === 'appeal' ? 'amber' : ''}`}>{kindLabels[help.kind]}</span><span className={`queue-status status-${help.status}`}>{helpStatusLabels[help.status]}</span><time>{at(help.created_at)}</time></div>
        <p>“{help.note}”</p>
        {help.claimed_by && <small>{help.claimed_by === actorId ? '由你接手' : '由其他老师接手'}{help.claimed_at && ` · ${at(help.claimed_at)}`}</small>}
        {help.resolution_note && <small>帮助记录：{help.resolution_note}</small>}
      </li>)}</ul>
      {focus?.status === 'waiting' && <button className="button primary full" disabled={sample || busy} onClick={() => void onClaim(focus)}><ShieldCheck size={15} />接手这条{kindLabels[focus.kind]}</button>}
      {mine && <div className="help-completion">
        <label>帮助记录<textarea placeholder="记录这次如何帮助孩子，以及仍未解决的部分…" value={helpNote} maxLength={1500} disabled={busy} onChange={event => setHelpNote(event.target.value)} /></label>
        <div className="button-row"><button className="button" disabled={busy || !helpNote.trim()} onClick={() => void complete('resolved')}>帮助已结束</button><button className="button" disabled={busy || !helpNote.trim()} onClick={() => void complete('unresolved')}>仍需帮助</button></div>
        <small><CircleHelp size={12} />结束帮助不会把题目标记为答对；对错请在下方复核。</small>
      </div>}
    </section>

    <section className="panel-section">
      <div className="section-heading"><span><History size={17} />作答过程</span><small>点选查看照片</small></div>
      <ol className="timeline">{chain.map(entry => <li key={entry.attempt.record.id} style={{ paddingLeft: Math.min(entry.depth, 4) * 6 }}>
        <span className={`timeline-dot ${entry.current ? 'current' : ''}`} />
        <button className={`attempt-entry ${entry.attempt.record.id === attemptId ? 'selected' : ''}`} onClick={() => onAttempt(entry.attempt.record.id)} aria-pressed={entry.attempt.record.id === attemptId}>
          <div className="timeline-title">{entry.label}{entry.current && <span className="tag">当前</span>}<time>{at(entry.attempt.record.occurred_at)}</time></div>
          {entry.parentLabel && <small>订正自：{entry.parentLabel}</small>}
          <p className="answer-line">{entry.attempt.record.answer_text ?? '（尚无可用识别文本）'}</p>
          <small><Camera size={11} /> {entry.attempt.record.asset_ids.length} 张照片 · {recognitionLabels[entry.attempt.record.recognition_status]} · {helpLabels[entry.attempt.record.help_condition]}</small>
        </button>
      </li>)}</ol>
    </section>

    <section className="panel-section">
      <div className="section-heading"><span><Lightbulb size={17} />提示记录</span><small>{ws.hints.length} 条</small></div>
      {ws.hints.length === 0 && <p className="subtle">还没有生成过提示。</p>}
      <ul className="hint-list">{ws.hints.map(hint => <li key={hint.id} className={hint.exposes_answer ? 'exposes' : ''}>
        <div className="help-row"><span className="tag">{hintLevels[hint.level]}</span>{hint.exposes_answer && <span className="tag rose">含答案</span>}{hint.validity !== 'active' && <span className="tag muted">{hint.validity === 'stale' ? '已失效' : '已撤回'}</span>}
          <time>{hint.first_shown_at ? `${at(hint.first_shown_at)} 看到` : '未记录展示'}</time></div>
        <p>{hint.content}</p><small>{hint.source_is_provisional ? '基于 AI 候选判断生成' : '基于老师确认的判断生成'}</small>
      </li>)}</ul>
    </section>

    <section className="panel-section review-form">
      <div className="section-heading"><span><FileCheck2 size={17} />现场复核</span></div>
      <p className="subtle">先核对原图，再记录依据。老师确认的结论会与 AI 候选分开保存。</p>
      {block && <div className="preview-note">{block}</div>}
      {!block && stale && <div className="preview-note">{stale}</div>}
      <label>处理方式<div className="select-wrap"><select value={action} disabled={locked} onChange={event => set({ action: event.target.value as SubmitReview['action'] })}>
        {decision ? <><option value="confirm">确认当前候选</option><option value="replace">更正当前判断</option><option value="leave_unresolved">保留待核实</option><option value="retract">撤销当前判断</option></> : <option value="manual_initial">人工初次核实</option>}
      </select><ChevronDown size={14} /></div></label>
      {action === 'confirm' && decision && <p className="subtle">将以老师身份确认：<strong>{verdictLabel[decision.verdict]}</strong></p>}
      {(action === 'replace' || action === 'manual_initial') && <label>核实结果<div className="select-wrap"><select value={form.verdict} disabled={locked} onChange={event => set({ verdict: event.target.value as Verdict })}><option value="">请选择结果</option><option value="correct">正确</option><option value="incorrect">需要订正</option><option value="partially_correct">部分正确</option><option value="unanswered">未作答</option></select><ChevronDown size={14} /></div></label>}
      <label>处理说明<textarea placeholder="说明为什么确认、更正或暂时无法判断…" value={form.reason} maxLength={2000} disabled={locked} onChange={event => set({ reason: event.target.value })} /></label>
      <label>原图核实依据<textarea placeholder="写下你在原图中核实的步骤或条件…" value={form.basis} maxLength={2000} disabled={locked} onChange={event => set({ basis: event.target.value })} /></label>
      {!locked && !stale && issue && (form.reason || form.basis) && <small className="form-issue">{issue}</small>}
      <button className="button primary full" disabled={!submittable} onClick={() => void submit()}>{busy ? <LoaderCircle size={16} className="spin" /> : <Check size={16} />}{busy ? '等待服务端确认…' : `提交：${actionLabels[action]}`}</button>
    </section>

    {ws.reviews.length > 0 && <section className="panel-section">
      <div className="section-heading"><span><UserCheck size={17} />复核记录</span><small>{ws.reviews.length} 条</small></div>
      <ul className="hint-list">{[...ws.reviews].sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at)).map(review => <li key={review.id}>
        <div className="help-row"><span className="tag">{actionLabels[review.action]}</span><time>{at(review.created_at)}</time></div><p>{review.reason}</p><small>{review.reviewer_id === actorId ? '由你提交' : '由其他老师提交'}</small>
      </li>)}</ul>
    </section>}
  </aside>
}
