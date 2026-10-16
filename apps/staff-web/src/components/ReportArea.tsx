import { useState } from 'react'
import { ArrowRight, FileText, Plus } from 'lucide-react'
import type { Report } from '../api/learningTypes'
import { finishedPeriods, periodFinished, periodLabel, reportStatusLabels } from '../learning'
import { LoadNote, RefreshButton, StudentPicker, useLoad, type LearningContext } from './LearningCommon'
import ReportDetail from './ReportDetail'

type Mode = 'draft' | 'published' | 'student'

function ReportItem({ report, name, selected, onSelect, extra }: { report: Report; name: string; selected: boolean; onSelect: () => void; extra?: string }) {
  return <button className={`queue-item ${selected ? 'selected' : ''}`} onClick={onSelect}>
    <div className="queue-item-top"><span className="queue-avatar"><FileText size={15} /></span>
      <span className={`tag ${report.status === 'draft' ? 'amber' : report.status === 'superseded' ? 'muted' : ''}`}>{reportStatusLabels[report.status]}</span>
      {report.supersedes_id && <span className="tag rose">更正版本</span>}<span className="queue-status muted">第 {report.version} 版</span></div>
    <h3>{name}</h3><p>{periodLabel(report.period_start, report.period_end)}{extra ? ` · ${extra}` : ''}</p>
    <footer><span>{report.published_at ? `发布于 ${report.published_at.slice(0, 10)}` : '尚未发布'}</span><ArrowRight size={15} /></footer>
  </button>
}

export default function ReportArea({ ctx, studentId, onStudent }: { ctx: LearningContext; studentId: string | null; onStudent: (id: string) => void }) {
  const [mode, setMode] = useState<Mode>('draft')
  const [chosen, setChosen] = useState<string | null>(null)
  const periods = finishedPeriods(Date.now(), 6)
  const [period, setPeriod] = useState(periods[0])
  const queue = useLoad(mode === 'student' ? null : mode, signal => ctx.learning.reportQueue(mode as 'draft' | 'published', signal), ctx.version)
  const own = useLoad(mode === 'student' ? studentId : null, signal => ctx.learning.studentReports(studentId!, signal), ctx.version)
  const names = new Map(ctx.students.map(student => [student.student_id, student.display_name]))
  const name = (id: string) => names.get(id) ?? '已授权学生'

  async function generate() {
    if (!studentId || !periodFinished(period, Date.now())) return
    const report = await ctx.act(() => ctx.learning.generateReport(studentId, period), '报告已按当前事实生成或更新。')
    if (report) setChosen(report.id)
  }

  return <main id="main" className="learning-area">
    <aside className="learning-side" aria-label="半月报告列表">
      <div className="queue-heading"><div><h2>半月报告</h2><p>每月 1–15 日、16 日–月末各一期（北京时间）</p></div><RefreshButton state={mode === 'student' ? own : queue} label="刷新报告列表" /></div>
      <div className="queue-switch" role="tablist" aria-label="报告列表">
        {([['draft', '待发布草稿'], ['published', '已发布'], ['student', '按学员']] as const).map(([value, text]) =>
          <button key={value} role="tab" aria-selected={mode === value} className={mode === value ? 'active' : ''} onClick={() => setMode(value)}>{text}</button>)}
      </div>
      {mode === 'student' && <div className="side-block">
        <StudentPicker students={ctx.students} value={studentId} onChange={onStudent} />
        {studentId && <div className="tool-form compact">
          <label>生成已结束的一期<div className="select-wrap"><select value={period} disabled={ctx.busy} onChange={event => setPeriod(event.target.value)}>
            {periods.map(value => <option key={value} value={value}>{periodLabel(value)}</option>)}</select></div></label>
          <button className="button" disabled={ctx.busy} onClick={() => void generate()}><Plus size={14} />生成 / 更新这一期</button>
          <small className="subtle">进行中的半月不能生成；已发布且事实未变时返回已发布版本。</small>
        </div>}
      </div>}
      <div className="queue-list">
        {mode === 'student' ? <LoadNote state={own}>
          {own.data?.items.map(report => <ReportItem key={report.id} report={report} name={name(report.student_id)} selected={report.id === chosen} onSelect={() => setChosen(report.id)}
            extra={report.snapshot?.sufficient ? `${report.snapshot.focus.length} 个具体问题` : '学科证据不足'} />)}
          {own.data && own.data.items.length === 0 && <div className="queue-empty"><p>这位学员还没有报告</p><span>每期结束后系统会自动生成草稿。</span></div>}
        </LoadNote> : <LoadNote state={queue}>
          {queue.data?.items.map(report => <ReportItem key={report.id} report={report} name={name(report.student_id)} selected={report.id === chosen} onSelect={() => setChosen(report.id)}
            extra={report.sufficient ? `${report.focus_count} 个具体问题` : '学科证据不足'} />)}
          {queue.data && queue.data.items.length === 0 && <div className="queue-empty"><p>{mode === 'draft' ? '没有待发布的草稿' : '还没有已发布的报告'}</p></div>}
        </LoadNote>}
      </div>
    </aside>
    <section className="learning-main">
      {chosen ? <ReportDetail key={chosen} ctx={ctx} reportId={chosen} studentName={name} onOpen={setChosen} />
        : <div className="learning-empty"><span className="round-icon"><FileText size={23} /></span><h2>每一期都有报告，有没有测验都一样</h2>
          <p>报告锁定本期已核实的事实和真实分母。草稿随事实更新；已发布的版本不会被改写，事实变化时会生成需要写明更正说明的新版本。家长通过可撤销的链接查看最新发布版本。</p></div>}
    </section>
  </main>
}
