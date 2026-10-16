import { useId, useMemo, useState } from 'react'
import { ArrowDownToLine, ArrowRight, BookOpen, ChevronRight, FileImage, History, PenLine, RefreshCw, Trash2 } from 'lucide-react'
import type { HomeworkSession } from '../core/types'
import type { Workspace } from '../useStudentWorkspace'
import { dateLabel, stageLabels } from '../presentation'
import { continueTarget, countsSentence, filterHistory, groupByDay, statusFilters, subjectOf, subjects, type HistoryFilter } from '../history'
import { LOCAL_TTL_MS } from '../core'

const ttlDays = Math.round(LOCAL_TTL_MS / 86_400_000)

export function LocalDataControl({ workspace }: { workspace: Workspace }) {
  const [confirming, setConfirming] = useState(false), id = useId()
  return <section className="local-data" aria-labelledby={id}>
    <div><h2 id={id}>本机草稿</h2><p className="muted">未上传的照片、草稿和尚未确认结果的提交只存在这台设备上，{ttlDays} 天未使用会自动清除。换设备或借用设备后，可以手动清除。服务器上已保存的作业不受影响。</p></div>
    {confirming ? <div className="local-data-confirm" role="group" aria-label="确认清除本机草稿"><p>确定清除吗？还没上传的照片和未确认的提交也会一起删除，无法恢复。</p><button className="button quiet" disabled={!!workspace.busy} onClick={() => setConfirming(false)}>先不清除</button><button className="button danger" disabled={!!workspace.busy} onClick={() => { void workspace.clearLocalData().then(() => setConfirming(false)) }}><Trash2 size={16} /> 确认清除</button></div>
      : <button className="button secondary" disabled={!!workspace.busy} onClick={() => setConfirming(true)}><Trash2 size={16} /> 清除本机草稿</button>}
  </section>
}

function HomeworkCard({ item, workspace, onOpen }: { item: HomeworkSession; workspace: Workspace; onOpen: (id: string, caseId?: string) => void }) {
  const summary = workspace.summaries[item.id], failed = workspace.summaryFailed.includes(item.id)
  const unresolvedCase = summary && (summary.counts.needs_correction || summary.counts.pending_review) ? summary.firstUnresolvedCaseId : null
  const counts = summary ? countsSentence(summary.counts) : failed ? '题目统计暂时读不到，打开后可以查看' : '正在统计题目…'
  const partial = item.coverage.collection_scope !== 'declared_complete'
  return <article className="history-card">
    <button className="history-open" disabled={!!workspace.busy} onClick={() => onOpen(item.id)} aria-label={`打开${item.title || '学校作业'}，${dateLabel(item.created_at)}`}>
      <span className="history-paper" aria-hidden="true"><BookOpen size={26} strokeWidth={1.3} /></span>
      <span className="history-info">
        <span className="history-meta"><time dateTime={item.created_at}>{dateLabel(item.created_at)}</time><span className="subject-tag">{subjectOf(item)}</span></span>
        <strong>{item.title || '学校作业'}</strong>
        <span className="history-counts" aria-live="polite">{counts}</span>
        {partial && <span className="muted small">只统计本次拍到的题目</span>}
        {summary && summary.retired > 0 && <span className="muted small">老师已重新切分部分题目，已换成新的题目</span>}
      </span>
      <ChevronRight size={19} aria-hidden="true" />
    </button>
    {unresolvedCase && <button className="button secondary history-continue" disabled={!!workspace.busy} aria-label={`继续订正${item.title || '学校作业'}`} onClick={() => onOpen(item.id, unresolvedCase)}><PenLine size={16} /> 继续订正</button>}
  </article>
}

export function HistoryPage({ workspace, onOpen, onOpenDraft, onNewWork }: { workspace: Workspace; onOpen: (id: string, caseId?: string) => void; onOpenDraft: (id: string) => void; onNewWork: () => void }) {
  const [filter, setFilter] = useState<HistoryFilter>({ subject: null, status: 'all' })
  const subjectId = useId()
  const items = workspace.history
  const options = useMemo(() => subjects(items), [items])
  const visible = filterHistory(items, workspace.summaries, filter)
  const groups = groupByDay(visible)
  const target = continueTarget(items, workspace.summaries)
  const counting = filter.status === 'all' ? 0 : items.filter(h => (!filter.subject || subjectOf(h) === filter.subject) && !workspace.summaries[h.id]).length
  const localOnly = workspace.drafts.filter(d => !d.id.startsWith('remote_') && (!d.homeworkId || !items.some(h => h.id === d.homeworkId)))
  const unfiltered = filter.status === 'all' && !filter.subject
  return <section className="history-page">
    <div className="page-title-row"><div><span className="eyebrow">我的学习足迹</span><h1>每一份认真，都留在这里。</h1><p>按日期查看之前的作业，也可以接着订正还没完成的题。</p></div><button className="button secondary" disabled={!!workspace.busy} onClick={() => void workspace.loadHistory()}><RefreshCw size={17} /> 刷新记录</button></div>
    {target && <div className="continue-banner"><div><strong>还有题目等你订正</strong><p className="muted">{items.find(h => h.id === target.homeworkId)?.title || '学校作业'} · {countsSentence(workspace.summaries[target.homeworkId]!.counts)}</p></div><button className="button primary" disabled={!!workspace.busy} onClick={() => onOpen(target.homeworkId, target.caseId)}><PenLine size={17} /> 继续订正</button></div>}
    {items.length > 0 && <div className="history-filters" role="search" aria-label="筛选历史作业">
      <label htmlFor={subjectId}>科目</label>
      <select id={subjectId} value={filter.subject ?? ''} onChange={event => setFilter(old => ({ ...old, subject: event.target.value || null }))}><option value="">全部科目</option>{options.map(s => <option key={s} value={s}>{s}</option>)}</select>
      <div className="segmented" role="group" aria-label="按状态筛选">{statusFilters.map(option => <button key={option.value} type="button" aria-pressed={filter.status === option.value} className={filter.status === option.value ? 'active' : ''} onClick={() => setFilter(old => ({ ...old, status: option.value }))}>{option.label}</button>)}</div>
    </div>}
    {counting > 0 && <p className="muted small filter-note" role="status">还有 {counting} 份作业正在统计，统计完成后才会出现在这个筛选里。</p>}
    {unfiltered && localOnly.length > 0 && <section className="history-day" aria-labelledby="local-drafts"><h2 id="local-drafts" className="day-label">这台设备上的草稿</h2><div className="history-grid">{localOnly.map(item => <article className="history-card" key={item.id}><button className="history-open" disabled={!!workspace.busy} onClick={() => onOpenDraft(item.id)}><span className="history-paper" aria-hidden="true"><FileImage size={26} strokeWidth={1.3} /></span><span className="history-info"><time dateTime={item.createdAt}>{dateLabel(item.createdAt)}</time><strong>{item.title}</strong><span className={`status-pill ${item.stage === 'failed' ? 'amber' : 'neutral'}`}>{stageLabels[item.stage]}</span></span><ChevronRight size={19} aria-hidden="true" /></button></article>)}</div></section>}
    {!items.length && !localOnly.length ? <div className="history-empty"><History size={37} strokeWidth={1.2} /><h2>第一份作业，从今天开始</h2><p>拍下自己的作答，检查和订正记录会保留在这里。</p><button className="button primary" onClick={onNewWork}>去拍一份作业 <ArrowRight size={17} /></button></div>
      : items.length > 0 && !groups.length ? <p className="history-none" role="status">没有符合条件的作业。可以换个科目或状态看看。</p>
      : groups.map(group => <section className="history-day" key={group.key} aria-labelledby={`day-${group.key}`}><h2 id={`day-${group.key}`} className="day-label">{group.label}<span className="muted">{group.items.length} 份</span></h2><div className="history-grid">{group.items.map(item => <HomeworkCard key={item.id} item={item} workspace={workspace} onOpen={onOpen} />)}</div></section>)}
    {workspace.cursor && <button className="button secondary load-more" disabled={!!workspace.busy} onClick={() => void workspace.loadHistory(true)}>再看一些记录 <ArrowDownToLine size={16} /></button>}
    <LocalDataControl workspace={workspace} />
  </section>
}
