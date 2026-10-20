import { useEffect, useState } from 'react'
import { ArrowLeft, ChevronRight, LoaderCircle, RefreshCw } from 'lucide-react'
import { safeError } from '../core'
import type { KnowledgeDetail, KnowledgeMap } from '../core/learning'
import type { Workspace } from '../useStudentWorkspace'
import { assistanceLabel, chapterCounts, dayLabel, eventLabel, groupStates, PENDING_NOTICE, reasonText, resultLabels, statusLabels } from '../learning'

/** The signed-in student's map, re-read after homework changes; null (no entry) on failure or for another student. */
export function useKnowledgeMap(workspace: Workspace) {
  const [map, setMap] = useState<KnowledgeMap | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [version, setVersion] = useState(0)
  const { client, studentId, recordVersion } = workspace
  useEffect(() => {
    if (!studentId) { setMap(null); setLoading(false); setError(''); return }
    let live = true
    setLoading(true); setError('')
    client.knowledgeMap().then(value => { if (live) { setMap(value); setLoading(false) } }, (e: unknown) => { if (live) { setMap(null); setError(safeError(e).message); setLoading(false); workspace.report(e) } })
    return () => { live = false }
  }, [client, studentId, recordVersion, version])
  return { map: map && map.student_id === studentId ? map : null, loading, error, refresh: () => setVersion(v => v + 1) }
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
function catalogStatusLabel(status: string) {
  if (status === 'available') return '官方目录已匹配'
  if (status === 'publisher_mismatch') return '出版社信息不一致'
  if (status === 'incomplete') return '册次信息不完整'
  return '官方目录待补'
}

export function KnowledgeMapView({ map, onOpen }: { map: KnowledgeMap; onOpen: (knowledgeId: string) => void }) {
  return <>
    {map.projection.pending && <p className="knowledge-pending" role="status"><RefreshCw size={15} /> {PENDING_NOTICE}</p>}
    {map.subjects && <section className="curriculum-overview" aria-label="各科最新版教材与作答样本">
      {map.subjects.map(subject => <article className="curriculum-subject" key={`${subject.grade}-${subject.subject}`}>
        <div className="curriculum-subject-heading"><div><h2>{subject.subject}</h2><p>{subject.publisher_name}</p></div>
          <span className={`status-pill ${subject.catalog_status === 'available' ? 'positive' : 'neutral'}`}>{catalogStatusLabel(subject.catalog_status)}</span></div>
        {subject.catalog_verified_at && <p className="curriculum-source-note">官方目录核对于 {dayLabel(subject.catalog_verified_at)}</p>}
        {subject.catalog_status === 'publisher_mismatch' && subject.catalog_candidates.length > 0 && <p className="curriculum-source-note">官方目录列出的版本：{subject.catalog_candidates.join('、')}。与指定出版社不一致，未用于统计。</p>}
        <div className="curriculum-score"><strong>{subject.score_percent === null ? '暂无样本' : `${subject.score_percent}%`}</strong><span>作业参考正确率</span></div>
        <p className="curriculum-counts">正确 {subject.correct_count} · 错误 {subject.incorrect_count} · 有效作答 {subject.scorable_count}</p>
        {subject.books.length > 0 ? <ul className="curriculum-books">{subject.books.map(book => <li key={book.term}>
          <span>{book.term === 'up' ? '上册' : book.term === 'down' ? '下册' : '全一册'}</span><strong>{book.title}</strong>
          {book.isbn && <small>ISBN {book.isbn}</small>}
          <small>{book.revised_2022_standard ? '2022 课标修订版' : '目录当前版本，未标注 2022 课标修订'}</small>
          {book.updated_at && <small>目录更新 {dayLabel(book.updated_at)}</small>}
        </li>)}</ul> : <p className="curriculum-source-note">暂未找到与指定出版社完全匹配的最新版官方书目，不使用旧版教材替代。</p>}
        {!subject.graph_ready && <p className="curriculum-source-note">知识点目录仍在按最新版教材校对，暂只累计本学科作答样本。</p>}
      </article>)}
    </section>}
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

export function KnowledgeMapPage({ workspace, map, loading, loadError, onRefresh }: { workspace: Workspace; map: KnowledgeMap | null; loading: boolean; loadError: string; onRefresh: () => void }) {
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
    <div className="page-title-row"><div><span className="eyebrow">按各出版社最新版教材统计</span><h1>我的知识图谱</h1><p>先看各科作业样本，再逐步补全对应教材知识点。</p></div>{!openId && <button className="button secondary" onClick={onRefresh}><RefreshCw size={17} /> 刷新图谱</button>}</div>
    <div className="curriculum-grade"><label htmlFor="curriculum-grade-select">当前年级</label><select id="curriculum-grade-select" value={map?.grade ?? ''} disabled={!!workspace.busy} onChange={event => { const grade = Number(event.target.value); if (grade) void workspace.setStudentGrade(grade).then(onRefresh) }}><option value="">请选择</option>{[5, 6, 7, 8].map(grade => <option value={grade} key={grade}>{grade <= 6 ? `小学${['', '', '', '', '', '五', '六'][grade]}年级` : `初${grade - 6}`}</option>)}</select></div>
    {openId ? error ? <div className="inline-error" role="alert"><p>{error}</p><button className="text-button" onClick={() => setOpenId(null)}><ArrowLeft size={14} /> 回到知识地图</button></div>
      : detail?.node.id === openId ? <KnowledgeDetailView detail={detail} onBack={() => setOpenId(null)} />
      : <p className="muted small" role="status"><LoaderCircle className="spin" size={14} /> 正在打开这个知识点…</p>
      : loading ? <p className="muted small" role="status"><LoaderCircle className="spin" size={15} /> 正在读取最新版课程范围…</p>
      : map ? <KnowledgeMapView map={map} onOpen={setOpenId} />
      : <div className="inline-error" role="alert"><p>{loadError || '暂时无法读取课程图谱。'}</p><button className="text-button" onClick={onRefresh}><RefreshCw size={15} /> 重试</button></div>}
  </section>
}
