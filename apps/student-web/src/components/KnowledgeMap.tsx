import { useEffect, useState } from 'react'
import { ArrowLeft, ChevronRight, LoaderCircle, RefreshCw } from 'lucide-react'
import { safeError } from '../core'
import type { KnowledgeDetail, KnowledgeMap } from '../core/learning'
import type { Workspace } from '../useStudentWorkspace'
import { assistanceLabel, chapterCounts, dayLabel, eventLabel, groupStates, PENDING_NOTICE, reasonText, resultLabels, statusLabels } from '../learning'

/** The signed-in student's map, re-read after homework changes; null (no entry) on failure or for another student. */
export function useKnowledgeMap(workspace: Workspace) {
  const [map, setMap] = useState<KnowledgeMap | null>(null)
  const [version, setVersion] = useState(0)
  const { client, studentId, recordVersion } = workspace
  useEffect(() => {
    if (!studentId) { setMap(null); return }
    let live = true
    client.knowledgeMap().then(value => { if (live) setMap(value) }, (e: unknown) => { if (live) { setMap(null); workspace.report(e) } })
    return () => { live = false }
  }, [client, studentId, recordVersion, version])
  return { map: map && map.student_id === studentId ? map : null, refresh: () => setVersion(v => v + 1) }
}

export function KnowledgeDetailView({ detail, onBack }: { detail: KnowledgeDetail; onBack: () => void }) {
  const status = detail.state ? statusLabels[detail.state.learning_status] : statusLabels.no_judgment
  return <section className="knowledge-detail" aria-labelledby="knowledge-detail-title">
    <button className="text-button" onClick={onBack}><ArrowLeft size={16} /> 回到知识地图</button>
    <div className="knowledge-detail-head"><h2 id="knowledge-detail-title">{detail.node.name}</h2><span className={`status-pill ${status.tone}`}>{status.label}</span></div>
    <p className="knowledge-reason">{reasonText(detail.state?.learning_status ?? 'no_judgment', detail.state?.reason_codes ?? [])}</p>
    {detail.node.observable_objective && <div className="knowledge-block"><h3>学完能做到</h3><p>{detail.node.observable_objective}</p></div>}
    {detail.prerequisites.length > 0 && <div className="knowledge-block"><h3>会用到之前学过的</h3><ul className="prerequisite-list">{detail.prerequisites.map(p => <li key={p.id}><span>{p.name}</span><span className={`status-pill ${statusLabels[p.learning_status].tone}`}>{statusLabels[p.learning_status].label}</span></li>)}</ul><p className="muted small">{detail.notice}</p></div>}
    <div className="knowledge-block"><h3>我的作答记录</h3>
      {detail.evidence.length ? <ol className="evidence-list">{detail.evidence.map(e => <li key={e.id}>
        <div className="evidence-meta"><time dateTime={e.occurred_at}>{dayLabel(e.occurred_at)}</time>{e.homework_title && <span>{e.homework_title}</span>}<span>{eventLabel(e.event_type)}</span></div>
        <p className="evidence-question">{e.question_text || '题目文字暂缺'}</p>
        <div className="evidence-tags"><span className="status-pill neutral">{resultLabels[e.result]}</span><span className="muted small">{assistanceLabel(e.assistance_level)}</span></div>
      </li>)}</ol> : <p className="muted small">还没有可用的作答记录。</p>}
    </div>
  </section>
}

/** The student's own map. Labels and reasons in words; counts per chapter, never a percentage or rank. */
export function KnowledgeMapView({ map, onOpen }: { map: KnowledgeMap; onOpen: (knowledgeId: string) => void }) {
  return <>
    {map.projection.pending && <p className="knowledge-pending" role="status"><RefreshCw size={15} /> {PENDING_NOTICE}</p>}
    {map.chapters.length > 0 && <ul className="chapter-rollup" aria-label="各章概况">{map.chapters.map(chapter => <li key={chapter.chapter_id}>
      <strong>{chapter.name}</strong>
      <span className="chapter-counts">{chapterCounts(chapter).map(c => <span key={c.status} className={`status-pill ${statusLabels[c.status].tone}`}>{c.label} {c.count}</span>)}</span>
    </li>)}</ul>}
    {groupStates(map.states).map(group => <section className="knowledge-group" key={group.id || 'other'} aria-labelledby={`chapter-${group.id || 'other'}`}>
      <h2 id={`chapter-${group.id || 'other'}`} className="day-label">{group.name}</h2>
      <div className="knowledge-list">{group.states.map(state => { const status = statusLabels[state.learning_status]; return <button key={state.knowledge_id} className="knowledge-item" onClick={() => onOpen(state.knowledge_id)}>
        <span className="knowledge-item-text"><strong>{state.name}</strong><span>{reasonText(state.learning_status, state.reason_codes)}</span></span>
        <span className={`status-pill ${status.tone}`}>{status.label}</span><ChevronRight size={16} aria-hidden="true" />
      </button> })}</div>
    </section>)}
    <p className="muted small knowledge-notice">{map.notice}</p>
  </>
}

export function KnowledgeMapPage({ workspace, map, onRefresh }: { workspace: Workspace; map: KnowledgeMap; onRefresh: () => void }) {
  const [openId, setOpenId] = useState<string | null>(null)
  const [detail, setDetail] = useState<KnowledgeDetail | null>(null)
  const [error, setError] = useState('')
  const { client } = workspace
  useEffect(() => {
    if (!openId) { setDetail(null); return }
    let live = true; setDetail(null); setError('')
    client.knowledgeNode(openId).then(value => { if (live) setDetail(value) }, (e: unknown) => { if (live) { setError(safeError(e).message); workspace.report(e) } })
    return () => { live = false }
  }, [openId, client])
  return <section className="knowledge-page">
    <div className="page-title-row"><div><span className="eyebrow">根据 AI 判定与老师核实的作业</span><h1>我的知识地图</h1><p>看看哪些地方在变熟练，哪些地方可以继续练习。</p></div>{!openId && <button className="button secondary" onClick={onRefresh}><RefreshCw size={17} /> 刷新地图</button>}</div>
    {openId ? error ? <div className="inline-error" role="alert"><p>{error}</p><button className="text-button" onClick={() => setOpenId(null)}><ArrowLeft size={14} /> 回到知识地图</button></div>
      : detail?.node.id === openId ? <KnowledgeDetailView detail={detail} onBack={() => setOpenId(null)} />
      : <p className="muted small" role="status"><LoaderCircle className="spin" size={14} /> 正在打开这个知识点…</p>
      : <KnowledgeMapView map={map} onOpen={setOpenId} />}
  </section>
}
