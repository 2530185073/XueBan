import { useEffect, useRef, useState } from 'react'
import { Check, Copy, FileText, Send, Share2, TriangleAlert } from 'lucide-react'
import type { FocusItem, Report, ReportSnapshot } from '../api/learningTypes'
import { dateTime, evidenceSourceCounts, openQuestionLabels, periodLabel, regeneratedLabels, reportStatusLabels, shareUrl, statusLabel } from '../learning'
import { LoadNote, RefreshButton, useLoad, type LearningContext } from './LearningCommon'
import { ShareLinkList } from './ShareLinkList'

const helpKinds: Record<string, string> = { question: '作业答疑', recognition: '识别有误', judgment: '判断有疑问', appeal: '申请重查' }

function Focus({ item }: { item: FocusItem }) {
  return <section className="report-block">
    <h4>{item.name} <span className="tag muted">期末观察：{statusLabel(item.status_at_period_end)}</span></h4>
    <p>本期在 <strong>{item.denominator}</strong> 次有效作答中出错 <strong>{item.errors}</strong> 次（其中独立完成时出错 {item.independent_errors} 次），答对 {item.counter_evidence} 次。</p>
    <p className="subtle">{evidenceSourceCounts(item)}</p>
    <p className="chip-row">{Object.entries(item.assistance).map(([label, n]) => <span key={label} className="chip">{label} × {n}</span>)}</p>
    {item.verified_scope.map(scope => <p key={scope}><span className="tag">已核实范围</span> {scope}</p>)}
    {item.possible_causes.map(cause => <p key={cause} className="subtle"><span className="tag amber">可能原因（未核实）</span> {cause}</p>)}
    {item.unknown.length > 0 && <p className="subtle">还不知道：{item.unknown.map(code => openQuestionLabels[code] ?? code).join('；')}</p>}
    <div className="evidence-cards">{item.cards.map(card => <dl key={card.evidence_id} className="evidence-card">
      <dt>日期</dt><dd>{card.date}{card.homework_title && ` · ${card.homework_title}`}{card.page !== null && ` · 第 ${card.page} 页`}</dd>
      <dt>题目</dt><dd>{card.question || '（题干待识别）'}</dd><dt>帮助条件</dt><dd>{card.assistance}</dd><dt>可信程度</dt><dd>{card.certainty}</dd>
      {card.staff_explanation && <><dt>老师说明</dt><dd>{card.staff_explanation}</dd></>}
    </dl>)}</div>
    <p><span className="field-title">最小下一步：</span>{item.next_step}</p>
  </section>
}

export function SnapshotView({ snap }: { snap: ReportSnapshot }) {
  const { coverage, school_tasks: tasks, help } = snap
  const optional = snap.sections.optional_activity
  return <div className="snapshot">
    <section className="report-block">
      <h4>学校任务概况</h4>
      {coverage.partial_period && <p className="preview-note">孩子于 {coverage.first_seen_date} 开始使用，本期不足完整周期，不与完整周期直接比较。</p>}
      <dl className="facts">
        <dt>有记录的到班日</dt><dd>{coverage.observed_days} 天</dd>
        <dt>拍照作业</dt><dd>{coverage.homeworks} 份，共 {tasks.questions} 道题</dd>
        <dt>老师已核实</dt><dd>{tasks.staff_verified} / {tasks.questions} 道</dd>
        <dt>已核实中</dt><dd>答对 {tasks.verdicts.correct} · 需要订正 {tasks.verdicts.incorrect} · 部分正确 {tasks.verdicts.partially_correct} · 未作答 {tasks.verdicts.unanswered}</dd>
        {tasks.ai_verdicts && <><dt>AI 初判（未核实）</dt><dd>{tasks.ai_judged ?? 0} 道：答对 {tasks.ai_verdicts.correct} · 需要订正 {tasks.ai_verdicts.incorrect} · 部分正确 {tasks.ai_verdicts.partially_correct} · 未作答 {tasks.ai_verdicts.unanswered}</dd></>}
        <dt>订正</dt><dd>完成 {tasks.corrected_resolved} 道 · 仍在订正 {tasks.correction_open} 道</dd>
        {!!tasks.ai_corrected_pending && <><dt>订正 AI 初判通过</dt><dd>{tasks.ai_corrected_pending} 道，尚未经老师核实</dd></>}
        <dt>待老师核实</dt><dd>{tasks.awaiting_verification} 道 AI 候选待确认 · {tasks.not_judged} 道尚无判断</dd>
      </dl>
    </section>
    {snap.focus.length > 0 && <><h3 className="block-title">具体问题</h3>{snap.focus.map(item => <Focus key={item.knowledge_id} item={item} />)}</>}
    {snap.steady.length > 0 && <section className="report-block"><h4>本期做得好的</h4><ul className="plain-list">{snap.steady.map(item =>
      <li key={item.knowledge_id}>{item.name}：答对 {item.correct} 次（独立完成 {item.independent_correct} 次；{evidenceSourceCounts(item)}）</li>)}</ul></section>}
    {!snap.sufficient && <section className="report-block"><h4>学科证据</h4><p>本期暂无足够的有效作答来判断具体知识点情况。这不表示孩子有薄弱，也不需要为此加做测验。</p></section>}
    {snap.unknown_current.length > 0 && <section className="report-block"><h4>还不知道的</h4><p>近期课程中的 {snap.unknown_current.map(item => item.name).join('、')} 本期没有足够作答记录，暂不判断。</p></section>}
    <section className="report-block"><h4>现场帮助</h4>
      <p>本期求助 {help.requests} 次（{Object.entries(help.by_kind).map(([kind, n]) => `${helpKinds[kind] ?? kind} ${n}`).join('、') || '无'}），帮助已结束 {help.resolved} 次；查看提示 {help.hints_viewed} 次，其中含答案 {help.answers_shown} 次。</p>
      {help.staff_help.length > 0 && <ul className="plain-list">{help.staff_help.map((item, i) => <li key={i}>{item.date} · {item.question || '（题干待识别）'} —— {item.note}{item.outcome === 'unresolved' ? '（仍需帮助）' : ''}</li>)}</ul>}
    </section>
    {optional && <section className="report-block"><h4>额外活动（自愿）</h4><p>{optional.summary}</p></section>}
    <p className="subtle limitations">{snap.limitations}</p>
  </div>
}

export default function ReportDetail({ ctx, reportId, studentName, onOpen }: { ctx: LearningContext; reportId: string; studentName: (id: string) => string; onOpen: (id: string) => void }) {
  const loaded = useLoad(reportId, signal => ctx.learning.report(reportId, signal), ctx.version)
  const report: Report | null = loaded.data
  const links = useLoad(report && report.status !== 'draft' ? reportId : null, signal => ctx.learning.shareLinks(reportId, signal), ctx.version)
  const [note, setNote] = useState('')
  const [step, setStep] = useState('')
  const [correction, setCorrection] = useState('')
  const [days, setDays] = useState(30)
  const [created, setCreated] = useState<{ id: string; url: string; expires: string } | null>(null)
  const [copied, setCopied] = useState(false)
  const urlInput = useRef<HTMLInputElement>(null)
  useEffect(() => { setNote(report?.staff_note ?? ''); setStep(report?.next_step ?? '') }, [report?.id, report?.revision])

  if (!report) return <div className="card"><LoadNote state={loaded} /></div>
  const draft = report.status === 'draft'
  const correctionVersion = !!report.supersedes_id
  const dirty = draft && (note.trim() !== (report.staff_note ?? '') || step.trim() !== (report.next_step ?? ''))
  const correctionIssue = correctionVersion && correction.trim().length < 4 ? '更正版本必须写明更正说明（至少 4 个字）。' : null

  async function save() {
    await ctx.act(() => ctx.learning.editReport(report!.id, { expected_revision: report!.revision, staff_note: note.trim() || null, next_step: step.trim() || null }), '老师的话已保存到草稿。')
  }
  async function publish() {
    if (dirty || correctionIssue) return
    const done = await ctx.act(() => ctx.learning.publishReport(report!.id, { expected_revision: report!.revision, correction_note: correctionVersion ? correction.trim() : null }),
      correctionVersion ? '更正版本已发布，家长链接会显示最新版本和更正说明。' : '报告已发布。')
    if (done) setCorrection('')
  }
  async function share() {
    const link = await ctx.act(() => ctx.learning.createShareLink(report!.id, days), '家长链接已生成。链接只显示这一次，请现在复制。')
    if (link) { setCopied(false); setCreated({ id: link.id, url: shareUrl(ctx.origin, link.path), expires: link.expires_at }) }
  }
  async function copy() {
    if (!created) return
    try { await navigator.clipboard.writeText(created.url) }
    catch { urlInput.current?.select(); return }
    setCopied(true)
    await ctx.act(() => ctx.learning.markMessageCopied(created.id), null)
  }

  return <div className="card report-detail">
    <div className="section-heading"><span><FileText size={17} />{studentName(report.student_id)} · {periodLabel(report.period_start, report.period_end)}</span><RefreshButton state={loaded} label="刷新报告" /></div>
    <div className="help-row wrap">
      <span className={`tag ${draft ? 'amber' : report.status === 'superseded' ? 'muted' : ''}`}>{reportStatusLabels[report.status]}</span><span className="tag muted">第 {report.version} 版</span>
      {correctionVersion && <span className="tag rose">更正版本</span>}
      {report.regenerated_reason && <small className="subtle">{regeneratedLabels[report.regenerated_reason] ?? report.regenerated_reason}</small>}
      {report.published_at && <small className="subtle">发布于 {dateTime(report.published_at)}</small>}
    </div>
    {report.status === 'superseded' && <p className="preview-note">此版本已被更正版本取代，家长链接显示最新发布的版本。</p>}
    {report.correction_note && <p className="preview-note">更正说明：{report.correction_note}</p>}
    {correctionVersion && report.supersedes_id && <button className="text-link" onClick={() => onOpen(report.supersedes_id!)}>查看被更正的上一版</button>}
    {report.snapshot && <SnapshotView snap={report.snapshot} />}

    <section className="report-block">
      <h4>老师的话</h4>
      {draft ? <div className="tool-form">
        <label>给家长的说明（可选，最多 500 字）<textarea value={note} maxLength={500} disabled={ctx.busy} onChange={event => setNote(event.target.value)} /></label>
        <label>下一步（可选，最多 300 字）<textarea value={step} maxLength={300} disabled={ctx.busy} placeholder="只写支持学校作业的最小一步，不安排额外练习" onChange={event => setStep(event.target.value)} /></label>
        <button className="button" disabled={ctx.busy || !dirty} onClick={() => void save()}><Check size={14} />保存草稿</button>
      </div> : <>{report.staff_note ? <p>{report.staff_note}</p> : <p className="subtle">未填写。</p>}{report.next_step && <p><span className="field-title">下一步：</span>{report.next_step}</p>}</>}
    </section>

    {draft && <section className="report-block publish">
      <h4><Send size={15} />发布</h4>
      <p className="subtle">发布后此版本不再改写；若之后本期事实变化，会生成需要写明更正说明的新版本。最新改判仍在汇总时不能发布。</p>
      {correctionVersion && <label>更正说明（必填，家长可见）<textarea value={correction} maxLength={500} disabled={ctx.busy} placeholder="例如：老师复核原图后，第 3 题改判为正确。" onChange={event => setCorrection(event.target.value)} /></label>}
      {dirty && <small className="form-issue">老师的话还没保存，请先保存草稿再发布。</small>}
      {correctionIssue && correction && <small className="form-issue">{correctionIssue}</small>}
      <button className="button primary" disabled={ctx.busy || dirty || !!correctionIssue} onClick={() => void publish()}><Send size={14} />{correctionVersion ? '发布更正版本' : '发布报告'}</button>
    </section>}

    {!draft && <section className="report-block">
      <h4><Share2 size={15} />家长链接</h4>
      <p className="subtle">家长无需账号。链接始终打开本期最新发布的版本及更正记录，可随时撤销。</p>
      {report.status === 'published' && <div className="button-row">
        <label className="inline-label">有效期<div className="select-wrap"><select value={days} disabled={ctx.busy} onChange={event => setDays(Number(event.target.value))}>
          <option value={7}>7 天</option><option value={30}>30 天</option><option value={90}>90 天</option></select></div></label>
        <button className="button" disabled={ctx.busy} onClick={() => void share()}><Share2 size={14} />生成新链接</button>
      </div>}
      {created && <div className="share-once" role="status">
        <p><TriangleAlert size={14} /> 链接只显示这一次，关闭或刷新后无法再查看；请现在复制给家长。有效至 {dateTime(created.expires)}。</p>
        <div className="button-row"><input ref={urlInput} readOnly value={created.url} aria-label="家长链接" onFocus={event => event.target.select()} />
          <button className="button primary" onClick={() => void copy()}><Copy size={14} />{copied ? '已复制' : '复制链接'}</button></div>
        <button className="text-link" onClick={() => setCreated(null)}>隐藏链接</button>
        <p className="subtle">发给家长后，在下面的链接记录里点“我已发给家长”。</p>
      </div>}
      <LoadNote state={links}>
        {links.data && <ShareLinkList ctx={ctx} items={links.data.items} />}
      </LoadNote>
    </section>}
  </div>
}
