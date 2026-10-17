import { useEffect, useRef, useState } from 'react'
import { Activity, LoaderCircle, RefreshCw } from 'lucide-react'
import { errorMessage, listAccounts } from './api'
import { QUEUE_REASONS, REVIEW_ACTIONS, TASK_KINDS, TASK_STATUSES, duration, label, loadMetrics, localWindow, percent, tally } from './metrics'
import type { ServiceMetrics as Metrics } from './metrics'
import type { AdminClient } from './types'
import './admin.css'

const RANGES = [1, 7, 14, 31]

export function ServiceMetrics({ client }: { client: AdminClient }) {
  const live = useRef(true)
  const request = useRef(0)
  const [campuses, setCampuses] = useState<string[]>([])
  const [campusId, setCampusId] = useState('')
  const [days, setDays] = useState(7)
  const [metrics, setMetrics] = useState<Metrics | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    live.current = true
    void listAccounts(client).then(list => {
      if (!live.current) return
      const ids = list.campuses.map(c => c.campus_id)
      setCampuses(ids); setCampusId(current => ids.includes(current) ? current : ids[0] ?? '')
      if (!ids.length) setLoading(false)
    }).catch(e => { if (live.current) { setError(errorMessage(e)); setLoading(false) } })
    return () => { live.current = false }
  }, [client])

  const load = async (campus: string, range: number) => {
    if (!campus) return
    const id = ++request.current
    setLoading(true); setError('')
    try {
      const next = await loadMetrics(client, campus, localWindow(range))
      if (live.current && id === request.current) setMetrics(next)
    } catch (e) { if (live.current && id === request.current) { setMetrics(null); setError(errorMessage(e)) } }
    finally { if (live.current && id === request.current) setLoading(false) }
  }
  useEffect(() => { void load(campusId, days) }, [campusId, days])

  const tasks = metrics ? Object.entries(metrics.tasks) : []
  return <section className="admin-accounts" aria-labelledby="service-metrics-title">
    <div className="page-title-row"><div><span className="eyebrow">校区管理 · 运行指标</span><h1 id="service-metrics-title">服务运行情况</h1><p>来自任务记录与老师处理记录的运行观察，用于排班与发现故障；不代表判题准确率或学生掌握程度。</p></div></div>
    <div className="admin-toolbar">
      <label>校区<select value={campusId} onChange={e => setCampusId(e.target.value)} disabled={campuses.length < 2}>{campuses.map(id => <option key={id} value={id}>{id}</option>)}</select></label>
      <label>范围<select value={days} onChange={e => setDays(Number(e.target.value))}>{RANGES.map(d => <option key={d} value={d}>{d === 1 ? '今天' : `近 ${d} 天（含今天）`}</option>)}</select></label>
      <button className="text-button" disabled={loading || !campusId} onClick={() => void load(campusId, days)}><RefreshCw size={15} /> 刷新</button>
    </div>
    {error && <div className="message error" role="alert"><span>{error}</span></div>}
    {!loading && !campuses.length && !error && <div className="admin-empty"><Activity size={31} strokeWidth={1.3} /><h3>没有已授权校区</h3><p>请先由系统管理员授予校区权限。</p></div>}
    {loading && <div className="admin-empty"><LoaderCircle className="spin" size={24} /><p>正在统计…</p></div>}
    {metrics && !loading && <>
      <div className="metric-cards" aria-label="当前待处理">
        <div className="metric-card"><span>当前待老师处理</span><strong>{metrics.current_backlog.review_queue_total}</strong><small>{tally(QUEUE_REASONS, metrics.current_backlog.review_queue)}</small></div>
        <div className="metric-card"><span>当前未完成求助</span><strong>{metrics.current_backlog.open_help_requests}</strong><small>不受统计范围影响</small></div>
        <div className="metric-card"><span>求助等待接手</span><strong>{duration(metrics.help_requests.wait_to_claim_seconds.p50)}</strong><small>中位数；95% 在 {duration(metrics.help_requests.wait_to_claim_seconds.p95)} 内</small></div>
        <div className="metric-card"><span>老师改判比例</span><strong>{percent(metrics.staff_reviews.overturn_share)}</strong><small>老师替换或撤回模型结论的占比</small></div>
      </div>
      <section className="admin-roster" aria-label="后台任务">
        <div className="admin-panel-title"><div><Activity size={19} /><h2>后台任务</h2></div></div>
        {tasks.length ? <div className="admin-table-scroll"><table className="admin-table"><thead><tr><th>任务</th><th>数量</th><th>结果</th><th>完成耗时（中位 / 95%）</th><th>失败原因</th></tr></thead><tbody>
          {tasks.map(([kind, row]) => <tr key={kind}>
            <td><strong>{label(TASK_KINDS, kind)}</strong></td>
            <td>{row.created}</td>
            <td>{tally(TASK_STATUSES, row.statuses)}</td>
            <td>{duration(row.end_to_end_seconds.p50)} / {duration(row.end_to_end_seconds.p95)}</td>
            <td>{tally({}, row.failure_codes)}</td>
          </tr>)}
        </tbody></table></div> : <div className="admin-empty"><p>这段时间没有后台任务。</p></div>}
      </section>
      <div className="metric-cards">
        <div className="metric-card"><span>模型给出判定</span><strong>{metrics.model_candidates.decisions}</strong><small>无法判断 {percent(metrics.model_candidates.unable_to_judge_share)}</small></div>
        <div className="metric-card"><span>老师核对</span><strong>{metrics.staff_reviews.reviews}</strong><small>{tally(REVIEW_ACTIONS, metrics.staff_reviews.actions)}</small></div>
        <div className="metric-card"><span>学生求助</span><strong>{metrics.help_requests.created}</strong><small>范围内未被接手 {metrics.help_requests.unclaimed_in_window}；处理完成中位 {duration(metrics.help_requests.wait_to_complete_seconds.p50)}</small></div>
      </div>
    </>}
  </section>
}
