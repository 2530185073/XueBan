import { useEffect, useRef, useState } from 'react'
import { ArrowRight, Check, CheckCheck, Copy, MessageSquareText, Plus, Send, TriangleAlert } from 'lucide-react'
import type { QueuedReport, Report, ReportSnapshot } from '../api/learningTypes'
import { dailyMessage, dateTime, dayLabel, deliveryLabel, evidenceSourceCounts, evidenceSourceLabel, recentDays, shareUrl } from '../learning'
import { LoadNote, RefreshButton, useLoad, type LearningContext } from './LearningCommon'
import { ShareLinkList } from './ShareLinkList'

const LINK_DAYS = 7

export function DailySnapshotView({ snap }: { snap: ReportSnapshot }) {
  const { coverage, school_tasks: tasks, help } = snap
  const pending = tasks.awaiting_verification + tasks.not_judged
  const kinds = snap.activity_kinds
  const optional = snap.sections.optional_activity
  return <div className="snapshot">
    {pending > 0 && <p className="preview-note"><TriangleAlert size={14} /> 还有 {pending} 道题未经老师核实。报告会分别标注 AI 初判与老师核实；尚不能判断的题不计作已确认对错。</p>}
    <section className="report-block">
      <h4>今天的作业</h4>
      {coverage.homeworks ? <dl className="facts">
        <dt>拍照作业</dt><dd>{coverage.homeworks} 份，共 {tasks.questions} 道题</dd>
        <dt>老师已核实</dt><dd>{tasks.staff_verified} 道：答对 {tasks.verdicts.correct} · 答错 {tasks.verdicts.incorrect} · 部分正确 {tasks.verdicts.partially_correct}</dd>
        {tasks.ai_verdicts && <><dt>AI 初判（未核实）</dt><dd>{tasks.ai_judged ?? 0} 道：答对 {tasks.ai_verdicts.correct} · 答错 {tasks.ai_verdicts.incorrect} · 部分正确 {tasks.ai_verdicts.partially_correct} · 未作答 {tasks.ai_verdicts.unanswered}</dd></>}
        <dt>订正</dt><dd>已完成 {tasks.corrected_resolved} 道 · 还没完成 {tasks.correction_open} 道</dd>
        {!!tasks.ai_corrected_pending && <><dt>订正 AI 初判通过</dt><dd>{tasks.ai_corrected_pending} 道，尚未经老师核实</dd></>}
      </dl> : <p className="subtle">今天没有拍照提交学校作业。</p>}
    </section>
    {(snap.mistakes ?? []).length > 0 && <section className="report-block"><h4>今天错的题和订正情况</h4>
      <div className="evidence-cards">{snap.mistakes!.map(item => <dl key={item.case_id} className="evidence-card">
        <dt>题目</dt><dd>{item.question || '（题目文字未识别）'}</dd>
        <dt>出处</dt><dd>{item.homework_title ?? '学校作业'}{item.page !== null && ` · 第 ${item.page} 页`}</dd>
        {item.verdict && <><dt>{evidenceSourceLabel(item.source_role)}</dt><dd>{item.verdict}</dd></>}
        <dt>订正</dt><dd><span className={`tag ${item.progress === 'verified_resolved' ? '' : 'amber'}`}>{item.correction}</span></dd>
      </dl>)}</div></section>}
    {snap.focus.length > 0 && <section className="report-block"><h4>今天卡住的知识点</h4><ul className="plain-list">{snap.focus.map(item =>
      <li key={item.knowledge_id}>{item.name}：{item.denominator} 次有效作答中出错 {item.errors} 次（{evidenceSourceCounts(item)}）{item.verified_scope.length > 0 && `（已核实：${item.verified_scope.join('；')}）`}</li>)}</ul></section>}
    {snap.steady.length > 0 && <section className="report-block"><h4>今天做得好的</h4><ul className="plain-list">{snap.steady.map(item => <li key={item.knowledge_id}>{item.name}（{evidenceSourceCounts(item)}）</li>)}</ul></section>}
    {(help.requests > 0 || help.hints_viewed > 0) && <section className="report-block"><h4>求助与提示</h4>
      <p>向老师求助 {help.requests} 次，查看提示 {help.hints_viewed} 次。</p>
      {help.staff_help.length > 0 && <ul className="plain-list">{help.staff_help.map((item, i) => <li key={i}>{item.question || '（题干待识别）'} —— {item.note}</li>)}</ul>}</section>}
    {kinds && (kinds.preview > 0 || kinds.variant > 0) && <section className="report-block"><h4>自愿活动</h4>
      <p>{[kinds.preview > 0 && `预习 ${kinds.preview} 次`, kinds.variant > 0 && `举一反三练习 ${kinds.variant} 次`].filter(Boolean).join('，')}
        {optional && `，共答 ${optional.answered ?? 0} 题、答对 ${optional.correct ?? 0} 题`}。不计入知识点判断。</p></section>}
    <p className="subtle limitations">{snap.limitations}</p>
  </div>
}

function DailyDetail({ ctx, reportId, name, day }: { ctx: LearningContext; reportId: string; name: string; day: string }) {
  const loaded = useLoad(reportId, signal => ctx.learning.report(reportId, signal), ctx.version)
  const report: Report | null = loaded.data
  const links = useLoad(report && report.status !== 'draft' ? reportId : null, signal => ctx.learning.shareLinks(reportId, signal), ctx.version)
  const [note, setNote] = useState('')
  const [step, setStep] = useState('')
  const [update, setUpdate] = useState('')
  const [message, setMessage] = useState<{ text: string; linkId: string } | null>(null)
  const [copied, setCopied] = useState(false)
  const box = useRef<HTMLTextAreaElement>(null)
  useEffect(() => { setNote(report?.staff_note ?? ''); setStep(report?.next_step ?? '') }, [report?.id, report?.revision])

  if (!report?.snapshot) return <div className="card"><LoadNote state={loaded} /></div>
  const draft = report.status === 'draft'
  const dirty = draft && (note.trim() !== (report.staff_note ?? '') || step.trim() !== (report.next_step ?? ''))
  const updateIssue = update.trim() && update.trim().length < 4 ? '更新说明至少 4 个字，或者留空。' : null

  async function copy(text: string, linkId: string) {
    try { await navigator.clipboard.writeText(text) }
    catch { setCopied(false); box.current?.select(); return }
    setCopied(true)
    await ctx.act(() => ctx.learning.markMessageCopied(linkId), null)
  }
  async function link(target: Report) {
    const created = await ctx.act(() => ctx.learning.createShareLink(target.id, LINK_DAYS), '消息已生成。链接只显示这一次，请复制发到家长微信。')
    if (!created) return
    const text = dailyMessage(name, day, shareUrl(ctx.origin, created.path))
    setMessage({ text, linkId: created.id })
    await copy(text, created.id)
  }
  async function confirmSent() {
    if (!message) return
    const done = await ctx.act(() => ctx.learning.confirmMessageSent(message.linkId, null), '已记录：老师确认已把消息发给家长。')
    if (done) setMessage(null)
  }
  async function send() {
    if (updateIssue) return
    let current: Report = report!
    if (dirty) {
      const saved = await ctx.act(() => ctx.learning.editReport(current.id, { expected_revision: current.revision, staff_note: note.trim() || null, next_step: step.trim() || null }), null)
      if (!saved) return
      current = saved
    }
    const published = await ctx.act(() => ctx.learning.publishReport(current.id, { expected_revision: current.revision, correction_note: update.trim() || null }), null)
    if (!published) return
    setUpdate('')
    await link(published)
  }

  return <div className="card report-detail">
    <div className="section-heading"><span><MessageSquareText size={17} />{name} · {dayLabel(day, Date.now())}</span><RefreshButton state={loaded} label="刷新反馈" /></div>
    <div className="help-row wrap">
      <span className={`tag ${draft ? 'amber' : report.status === 'superseded' ? 'muted' : ''}`}>{draft ? '草稿' : report.status === 'published' ? '已发布' : '已被新版本取代'}</span>
      {report.version > 1 && <span className="tag muted">第 {report.version} 版</span>}
      {report.published_at && <small className="subtle">发布于 {dateTime(report.published_at)}</small>}
      {draft && <small className="subtle">草稿会随作业和老师核实自动更新</small>}
    </div>
    {draft && report.supersedes_id && <p className="preview-note">上一版发布后，今天的记录又有变化（新作业、订正或改判）。这是更新草稿；核对并发布后，已生成的家长链接才会显示新版。是否已发送以发送记录为准。</p>}
    <DailySnapshotView snap={report.snapshot} />

    <section className="report-block">
      <h4>老师的话</h4>
      {draft ? <div className="tool-form">
        <label>今天的表现（可选，最多 500 字）<textarea value={note} maxLength={500} disabled={ctx.busy} placeholder="例如：今天写作业很专注，计算题比上周细心。" onChange={event => setNote(event.target.value)} /></label>
        <label>明天留意（可选，最多 300 字）<textarea value={step} maxLength={300} disabled={ctx.busy} placeholder="只写跟学校作业相关的一小步，不布置额外练习" onChange={event => setStep(event.target.value)} /></label>
      </div> : <>{report.staff_note ? <p>{report.staff_note}</p> : <p className="subtle">未填写。</p>}{report.next_step && <p><span className="field-title">明天留意：</span>{report.next_step}</p>}</>}
    </section>

    {draft && <section className="report-block publish">
      <h4><Send size={15} />发送给家长</h4>
      {report.supersedes_id && <label>更新说明（可选，家长可见）<textarea value={update} maxLength={500} disabled={ctx.busy} placeholder="例如：晚上补交了数学作业，已加进来。" onChange={event => setUpdate(event.target.value)} /></label>}
      {updateIssue && <small className="form-issue">{updateIssue}</small>}
      <p className="subtle">点一下会保存老师的话、发布并生成 {LINK_DAYS} 天有效的家长链接，同时把整段消息复制好。粘贴发到家长微信后，再点“我已发给家长”留下记录。</p>
      <button className="button primary" disabled={ctx.busy || !!updateIssue} onClick={() => void send()}><Send size={14} />{report.supersedes_id ? '发布更新并复制消息' : '发布并复制家长消息'}</button>
    </section>}

    {report.status === 'published' && !message && <section className="report-block">
      <h4><Copy size={15} />再发一次</h4>
      <p className="subtle">链接只在生成时显示一次。家长没收到或找不到时，可以生成新链接并复制消息。</p>
      <button className="button" disabled={ctx.busy} onClick={() => void link(report)}><Copy size={14} />生成新链接并复制消息</button>
    </section>}

    {message && <div className="share-once" role="status">
      <p><TriangleAlert size={14} /> {copied ? '消息已复制，粘贴到家长微信即可。' : '浏览器没允许自动复制，请选中下面的文字手动复制。'}链接只显示这一次。</p>
      <textarea ref={box} readOnly value={message.text} aria-label="发给家长的消息" rows={3} onFocus={event => event.target.select()} />
      <div className="button-row"><button className="button" disabled={ctx.busy} onClick={() => void copy(message.text, message.linkId)}><Copy size={14} />{copied ? '已复制' : '复制消息'}</button>
        <button className="button primary" disabled={ctx.busy} onClick={() => void confirmSent()}><CheckCheck size={14} />我已发给家长</button>
        <button className="text-link" onClick={() => setMessage(null)}>先不发，隐藏消息</button></div>
    </div>}

    {!draft && <LoadNote state={links}>
      {links.data && <section className="report-block"><h4>已生成的链接</h4>
        <p className="subtle">“已复制”和“老师已确认发送”是老师这边的记录，不代表家长已收到；家长点开链接后才显示“家长已打开”。</p>
        <ShareLinkList ctx={ctx} items={links.data.items} /></section>}
    </LoadNote>}
  </div>
}

export default function DailyFeedbackArea({ ctx }: { ctx: LearningContext }) {
  const days = recentDays(Date.now(), 7)
  const [day, setDay] = useState(days[0])
  const [chosen, setChosen] = useState<string | null>(null)
  const drafts = useLoad(`draft:${day}`, signal => ctx.learning.reportQueue('draft', signal, day), ctx.version)
  const sent = useLoad(`published:${day}`, signal => ctx.learning.reportQueue('published', signal, day), ctx.version)
  const byStudent = new Map<string, QueuedReport>()
  for (const report of sent.data?.items ?? []) byStudent.set(report.student_id, report)
  for (const report of drafts.data?.items ?? []) byStudent.set(report.student_id, report)
  const order = (report: QueuedReport | null) => !report ? 2 : report.status === 'draft' ? 0 : 1
  const rows = ctx.students.map(student => ({ student, report: byStudent.get(student.student_id) ?? null }))
    .sort((a, b) => order(a.report) - order(b.report) || a.student.display_name.localeCompare(b.student.display_name, 'zh-CN'))
  const waiting = rows.filter(row => row.report?.status === 'draft').length
  const published = rows.filter(row => row.report?.status === 'published').length
  const confirmed = rows.filter(row => row.report?.delivery?.sent_confirmed_at || row.report?.delivery?.opened_at).length
  const selected = rows.find(row => row.student.student_id === chosen)

  async function generate(studentId: string) {
    const report = await ctx.act(() => ctx.learning.generateDaily(studentId, day), '已按这一天的记录生成草稿。')
    if (report) setChosen(studentId)
  }

  return <main id="main" className="learning-area">
    <aside className="learning-side" aria-label="每日反馈列表">
      <div className="queue-heading"><div><h2>每日家长反馈</h2><p>孩子当天有作业或活动就自动生成草稿；老师核对后发布，复制消息发给家长并确认</p></div><RefreshButton state={drafts} label="刷新每日反馈" /></div>
      <div className="side-block">
        <label>日期<div className="select-wrap"><select value={day} onChange={event => { setDay(event.target.value); setChosen(null) }}>
          {days.map(value => <option key={value} value={value}>{dayLabel(value, Date.now())}</option>)}</select></div></label>
        <p className="subtle">待发布 {waiting} 人 · 已发布 {published} 人（确认发送 {confirmed} 人）· 共 {rows.length} 人</p>
      </div>
      <div className="queue-list">
        <LoadNote state={drafts.error ? drafts : sent}>
          {rows.map(({ student, report }) => {
            const [label, tone] = deliveryLabel(report)
            return <div key={student.student_id} className={`queue-item ${student.student_id === chosen ? 'selected' : ''}`}>
              <div className="queue-item-top"><span className="queue-avatar"><MessageSquareText size={15} /></span><span className={`tag ${tone}`}>{label}</span>
                {report && report.mistake_count > 0 && <span className="queue-status muted">{report.mistake_count} 道要订正</span>}</div>
              <h3>{student.display_name}</h3>
              {report ? <button className="text-link" onClick={() => setChosen(student.student_id)}>{report.status === 'draft' ? '核对并发布' : '查看'}<ArrowRight size={14} /></button>
                : <button className="text-link" disabled={ctx.busy} onClick={() => void generate(student.student_id)}><Plus size={14} />仍要生成</button>}
            </div>
          })}
          {rows.length === 0 && <div className="queue-empty"><p>没有授权的学员</p></div>}
        </LoadNote>
      </div>
    </aside>
    <section className="learning-main">
      {selected?.report ? <DailyDetail key={selected.report.id} ctx={ctx} reportId={selected.report.id} name={selected.student.display_name} day={day} />
        : <div className="learning-empty"><span className="round-icon"><Check size={23} /></span><h2>今天交了什么、错在哪、订正了没有</h2>
          <p>每份反馈只汇总这一天在托管班拍照提交的作业，对错以老师核实为准。草稿随作业和核实自动更新；发布后记录变化会生成更新草稿。老师核对并发布新版后，已生成的家长链接才显示新内容。</p></div>}
    </section>
  </main>
}
