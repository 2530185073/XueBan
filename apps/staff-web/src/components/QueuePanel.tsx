import { useState } from 'react'
import { ArrowRight, CircleHelp, ClipboardCheck, Inbox, MessageCircle, RefreshCw, RotateCcw, Search } from 'lucide-react'
import type { HelpRequest } from '../api/contracts.generated'
import type { ReviewQueueItem } from '../api/types'
import {
  activeHelpByCase, filterHelp, filterReview, helpStatusLabels, isSelected, kindLabels, orderHelp, progressLabels, reasonLabels,
  type HelpFilter, type QueueState, type QueueTab, type Selection,
} from '../queue'

const clock = (value: string) => new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }).format(new Date(value))

export default function QueuePanel({ tab, help, review, allCases, names, selection, onSelect, loading, busy, onRefresh, onMore, sample }: {
  tab: QueueTab
  help: QueueState<HelpRequest>; review: QueueState<ReviewQueueItem>; allCases: QueueState<ReviewQueueItem>; names: Map<string, string>
  selection: Selection | null; onSelect: (selection: Selection) => void
  loading: boolean; busy: boolean; onRefresh: () => void; onMore: (tab: QueueTab) => void; sample: boolean
}) {
  const [search, setSearch] = useState('')
  const [filter, setFilter] = useState<HelpFilter>('all')
  const helps = orderHelp(filterHelp(help.items, filter, search, names))
  const reviews = filterReview(tab === 'all' ? allCases.items : review.items, search)
  const helping = activeHelpByCase(help.items)
  const state = tab === 'help' ? help : tab === 'all' ? allCases : review
  const empty = tab === 'help' ? helps.length === 0 : reviews.length === 0

  return <aside className="queue-panel" aria-label="求助与待核实队列">
    <div className="queue-heading"><div><h2>今日待办</h2><p>{sample ? '合成记录 · 不调用后台' : '当前账号授权校区内的记录'}</p></div>
      <button className="icon-button" aria-label="刷新队列" title="刷新队列" disabled={sample || loading || busy} onClick={onRefresh}><RefreshCw size={17} className={loading ? 'spin' : ''} /></button></div>
    <label className="search-box"><Search size={16} /><input aria-label="搜索学生、作业或内容" placeholder={tab === 'help' ? '搜索学生或求助内容' : '搜索学生或作业'} value={search} onChange={event => setSearch(event.target.value)} /></label>
    {tab === 'help' && <div className="queue-tabs" aria-label="筛选求助状态">{([['all', '全部'], ['waiting', '待接手'], ['claimed', '处理中']] as const).map(([value, label]) =>
      <button key={value} aria-pressed={filter === value} className={filter === value ? 'active' : ''} onClick={() => setFilter(value)}>{label}</button>)}</div>}

    <div className="queue-list">
      {tab === 'help' && helps.map(item => <button key={item.id} className={`queue-item ${isSelected(selection, item.case_id, item.id) ? 'selected' : ''}`} disabled={busy} onClick={() => onSelect({ caseId: item.case_id, helpId: item.id })}>
        <div className="queue-item-top"><span className={`queue-avatar ${item.kind === 'appeal' ? 'appeal' : ''}`}>{item.kind === 'appeal' ? <RotateCcw size={16} /> : <MessageCircle size={16} />}</span>
          <span className={`tag ${item.kind === 'appeal' ? 'amber' : ''}`}>{kindLabels[item.kind]}</span><span className={`queue-status status-${item.status}`}>{helpStatusLabels[item.status]}</span></div>
        <h3>{names.get(item.case_id) ?? '学生作业'}</h3><p>{item.note}</p>
        <footer><span>{clock(item.created_at)} 发起</span><ArrowRight size={15} /></footer>
      </button>)}
      {tab !== 'help' && reviews.map(item => <button key={item.case_id} className={`queue-item ${isSelected(selection, item.case_id) ? 'selected' : ''}`} disabled={busy} onClick={() => onSelect({ caseId: item.case_id, helpId: null })}>
        <div className="queue-item-top"><span className="queue-avatar"><ClipboardCheck size={16} /></span><span className={`tag ${item.reason === 'verify_candidate' ? '' : 'amber'}`}>{reasonLabels[item.reason]}</span>
          {helping.has(item.case_id) && <span className="tag rose">有求助</span>}<span className="queue-status muted">{progressLabels[item.progress] ?? item.progress}</span></div>
        <h3>{item.student_name}</h3><p>{item.homework_title ?? '未命名作业'}</p>
        <footer><span>{clock(item.updated_at)} 更新</span><ArrowRight size={15} /></footer>
      </button>)}
      {empty && <div className="queue-empty"><Inbox size={30} strokeWidth={1.2} /><p>{loading && !state.loaded ? '正在读取队列…' : search || (tab === 'help' && filter !== 'all') ? '当前筛选下没有记录' : tab === 'help' ? '暂时没有孩子求助' : tab === 'all' ? '还没有已归档的题目' : '暂时没有需要核实的题'}</p><span>只列出已加载的记录；未加载的不算已处理。</span></div>}
      {state.cursor && <button className="button full" disabled={loading || busy || sample} onClick={() => onMore(tab)}>加载更多</button>}
    </div>
    <div className="queue-footer"><CircleHelp size={16} /><p>{tab === 'help' ? '申请重查排在最前，其余按等待时间排序。' : tab === 'all' ? '全部题保留 AI 初判与核实记录，可主动查看原图。AI 核对未完成不代表孩子答错。' : '只列出需要看原图或修订后继续处理的题。清晰的 AI 初判保留在全部题，无需逐题确认。'}</p></div>
  </aside>
}
