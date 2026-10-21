import { useEffect, useId, useMemo, useRef, useState } from 'react'
import { ArrowLeft, ArrowUpRight, BookOpen, ChevronRight, CircleDot, Clock3, Compass, Leaf, List, LoaderCircle, Map as MapIcon, RefreshCw, X } from 'lucide-react'
import { safeError } from '../core'
import { CurriculumBookPicker } from './CurriculumBookPicker'
import type { EvidenceSources, KnowledgeDetail, KnowledgeGraphEdge, KnowledgeGraphNode, KnowledgeMap, MappingCoverage } from '../core/learning'
import type { Workspace } from '../useStudentWorkspace'
import { assistanceLabel, dayLabel, eventLabel, GRAPH_NODE_HEIGHT, GRAPH_NODE_WIDTH, knowledgeChapters, knowledgeNodes, layoutKnowledgeGraph, nodeNameLines, PENDING_NOTICE, reasonText, resultLabels, statusLabels } from '../learning'

type OpenHomework = (homeworkId: string, caseId: string) => void
const sourceLabel = (source: EvidenceSources['assessment_source']) => source === 'model' ? 'AI 初判' : source === 'staff' ? '老师核实' : source === 'mixed' ? 'AI + 老师' : source === 'none' ? '待积累记录' : '来源待核实'

const mappingStateText = { no_direct_match: '暂未找到直接对应的教材知识点', mapping_pending: '知识点归类尚未完成', not_linked: '尚未形成有效的知识点关联' }

export function MappingCoverageNote({ coverage, onOpenHomework }: { coverage?: MappingCoverage; onOpenHomework?: OpenHomework }) {
  if (!coverage?.unlinked) return null
  return <section className="knowledge-mapping-gap" aria-label="尚未关联的错题">
    <div><span className="eyebrow">学习足迹 · 仍待归类</span><h3>还有 {coverage.unlinked} 道错题，尚未关联知识点</h3>
      <p>当前教材范围内，仍判错或部分正确的 {coverage.total} 道题中，{coverage.linked} 道已关联。原题和订正记录都还在。</p></div>
    <details><summary>看看哪些题</summary><ul>{coverage.items.map(item => <li key={item.case_id}>
      <span><strong>{item.subject}{item.label ? ` · ${item.label}` : ''}</strong><span className="mapping-question">{item.stem_preview}</span>
        <small>{item.source_role === 'model' ? 'AI 初判，尚未经老师核实' : '老师已核实'} · {mappingStateText[item.state ?? 'not_linked']}</small></span>
      {onOpenHomework && <button className="text-button" onClick={() => onOpenHomework(item.homework_id, item.case_id)}>回看原题 <ArrowUpRight size={15} /></button>}
    </li>)}</ul>{coverage.truncated && <p className="small muted">这里列出最近 30 道，总数包含全部符合范围的题目。</p>}<p className="small muted">{coverage.notice}</p></details>
  </section>
}

export function EvidenceSourceNote({ sources }: { sources: EvidenceSources | null | undefined }) {
  if (!sources?.assessment_source || sources.assessment_source === 'none') return null
  return <small className="knowledge-source-note">{sources.assessment_source === 'model' ? '根据 AI 初判，尚未经老师核实' : sources.assessment_source === 'staff' ? '根据老师核实的作答' : '包含 AI 初判与老师核实的作答'}</small>
}

export function useKnowledgeMap(workspace: Workspace) {
  const [map, setMap] = useState<KnowledgeMap | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [version, setVersion] = useState(0)
  const { client, studentId, recordVersion } = workspace
  useEffect(() => {
    if (!studentId) { setMap(null); setLoading(false); setError(''); return }
    let live = true
    setMap(null); setLoading(true); setError('')
    client.knowledgeMap().then(value => { if (live) { setMap(value); setLoading(false) } }, (e: unknown) => { if (live) { setMap(null); setError(safeError(e).message); setLoading(false); workspace.report(e) } })
    return () => { live = false }
  }, [client, studentId, recordVersion, version])
  return { map: map && map.student_id === studentId ? map : null, loading, error, refresh: () => setVersion(v => v + 1) }
}

export function KnowledgeDetailView({ detail, onBack, pending = false, onOpenNode, onOpenHomework, busy = false }: {
  detail: KnowledgeDetail; onBack: () => void; pending?: boolean
  onOpenNode?: (id: string) => void; onOpenHomework?: OpenHomework; busy?: boolean
}) {
  const heading = useRef<HTMLHeadingElement>(null)
  useEffect(() => { heading.current?.focus() }, [detail.node.id])
  const updating = pending || !!detail.projection?.pending
  const state = updating ? null : detail.state
  const status = statusLabels[state?.learning_status ?? 'no_judgment']
  const evidence = updating ? [] : [...detail.evidence].sort((a, b) => b.occurred_at.localeCompare(a.occurred_at))
  return <section className="knowledge-detail" aria-labelledby="knowledge-detail-title">
    <div className="knowledge-detail-top"><span className="eyebrow">这一点，我走到了哪里</span><button className="icon-button" onClick={onBack} aria-label="关闭知识点详情"><X size={19} /></button></div>
    <h2 id="knowledge-detail-title" ref={heading} tabIndex={-1}>{detail.node.name}</h2>
    <div className="knowledge-detail-status"><span className={`status-pill ${updating ? 'neutral' : status.tone}`}>{updating ? '正在更新' : status.label}</span>{!updating && <span className="knowledge-source-chip">{sourceLabel(state ? state.assessment_source : 'none')}</span>}</div>
    <p className="knowledge-reason">{updating ? PENDING_NOTICE : reasonText(state?.learning_status ?? 'no_judgment', state?.reason_codes ?? [])}</p>
    {!updating && <EvidenceSourceNote sources={state} />}
    {detail.node.observable_objective && <div className="knowledge-objective"><BookOpen size={17} /><div><h3>学会这一点，可以…</h3><p>{detail.node.observable_objective}</p></div></div>}
    <div className="knowledge-block"><div className="knowledge-section-heading"><h3>从作答中看见的变化</h3>{!updating && <span>{evidence.length} 条记录</span>}</div>
      {updating ? <div className="knowledge-detail-empty" role="status"><RefreshCw size={22} /><p>正在整理新的作答记录</p><small>完成后再看这次变化。</small></div>
        : evidence.length ? <ol className="evidence-list">{evidence.map(e => <li key={e.id}>
          <div className="evidence-meta"><time dateTime={e.occurred_at}>{dayLabel(e.occurred_at)}</time><span>{eventLabel(e.event_type)}</span><span className={`evidence-source ${e.source_role === 'sitting_teacher' ? 'staff' : ''}`}>{e.source_role === 'model' ? 'AI 初判' : e.source_role === 'sitting_teacher' ? '老师核实' : '来源待核实'}</span></div>
          {e.homework_title && <p className="evidence-homework">{e.homework_title}</p>}
          <p className="evidence-question">{e.question_text || '这条记录暂未附题目文字。'}</p>
          <div className="evidence-tags"><span className="status-pill neutral">{resultLabels[e.result]}</span><span className="muted small">{assistanceLabel(e.assistance_level)}</span></div>
          {e.homework_id && e.case_id && onOpenHomework && <button className="text-button evidence-open" disabled={busy} onClick={() => onOpenHomework(e.homework_id!, e.case_id!)}>查看原题与订正 <ArrowUpRight size={15} /></button>}
        </li>)}</ol>
        : <div className="knowledge-detail-empty"><Leaf size={25} strokeWidth={1.4} /><p>这里还没有可用的作答记录</p><small>以后遇到相关学校作业，再慢慢补全。</small></div>}
    </div>
    {detail.prerequisites.length > 0 && <div className="knowledge-block"><h3>会用到的基础</h3><ul className="prerequisite-list">{detail.prerequisites.map(p => <li key={p.id}>
      {onOpenNode ? <button onClick={() => onOpenNode(p.id)}><span>{p.name}</span><ChevronRight size={15} /></button> : <span>{p.name}</span>}
      {!updating && <span className={`status-pill ${statusLabels[p.learning_status].tone}`}>{statusLabels[p.learning_status].label}</span>}
    </li>)}</ul><p className="muted small">有关联，不代表这些知识点也有困难。</p></div>}
    <p className="muted small knowledge-notice">{detail.notice}</p>
    <button className="text-button knowledge-detail-back" onClick={onBack}><ArrowLeft size={16} /> 回到知识地图</button>
  </section>
}

function KnowledgeGraph({ nodes, edges, pending, selectedId, onOpen }: {
  nodes: KnowledgeGraphNode[]; edges: KnowledgeGraphEdge[]; pending: boolean; selectedId?: string | null; onOpen: (id: string) => void
}) {
  const markerId = useId().replace(/:/g, '')
  const layout = useMemo(() => layoutKnowledgeGraph(nodes, edges), [nodes, edges])
  const buttons = useRef(new Map<string, SVGGElement>())
  return <div className="knowledge-canvas" aria-label="可滑动查看的知识地图">
    <svg className="knowledge-graph" viewBox={`0 0 ${layout.width} ${layout.height}`} role="group" aria-label={`${nodes.length} 个知识点，按 Tab 浏览并按回车查看证据`}>
      <defs><marker id={markerId} viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 1 1 L 7 4 L 1 7" /></marker></defs>
      <g className="knowledge-edges" aria-hidden="true">{layout.paths.map(({ edge, path }) => <path key={edge.id} d={path} markerEnd={`url(#${markerId})`} />)}</g>
      {layout.positions.map(({ node, x, y }, index) => {
        const status = statusLabels[node.learning_status], lines = nodeNameLines(node.name)
        return <g key={node.id} ref={element => { if (element) buttons.current.set(node.id, element); else buttons.current.delete(node.id) }}
          transform={`translate(${x} ${y})`} role="button" tabIndex={0} aria-pressed={selectedId === node.id}
          aria-label={`${node.name}，${pending ? '正在更新' : `${status.label}，${sourceLabel(node.assessment_source)}`}，查看作答证据`}
          className={`knowledge-graph-node ${pending ? 'pending' : node.learning_status} ${selectedId === node.id ? 'selected' : ''}`}
          onClick={() => onOpen(node.id)} onKeyDown={event => {
            if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onOpen(node.id) }
            const offset = event.key === 'ArrowRight' || event.key === 'ArrowDown' ? 1 : event.key === 'ArrowLeft' || event.key === 'ArrowUp' ? -1 : 0
            if (offset) { event.preventDefault(); const next = layout.positions[(index + offset + layout.positions.length) % layout.positions.length]; buttons.current.get(next.node.id)?.focus() }
          }}>
          <title>{node.name}</title><rect className="knowledge-node-card" width={GRAPH_NODE_WIDTH} height={GRAPH_NODE_HEIGHT} rx="13" />
          <circle className="knowledge-node-dot" cx="20" cy="22" r="4" /><text className="knowledge-node-status" x="31" y="26">{pending ? '正在更新' : status.label}</text>
          {lines.map((line, lineIndex) => <text key={lineIndex} className="knowledge-node-title" x="18" y={lines.length === 1 ? 57 : 49 + lineIndex * 21}>{line}</text>)}
          <text className="knowledge-node-source" x="18" y="88">{pending ? '等待新记录整理' : sourceLabel(node.assessment_source)}</text>
          <path className="knowledge-node-arrow" d="M 166 82 L 172 82 M 169 79 L 172 82 L 169 85" />
        </g>
      })}
    </svg>
  </div>
}

function MapEmpty({ pending, hasScope }: { pending: boolean; hasScope: boolean }) {
  return <div className="knowledge-empty"><div className="knowledge-empty-symbol"><BookOpen size={35} strokeWidth={1.3} /><span><Leaf size={17} /></span></div><span className="eyebrow">每个人，都有自己的节奏</span>
    <h2>{pending ? '正在整理你的学习足迹' : hasScope ? '这张地图，还在慢慢准备' : '从自己的学校作业开始'}</h2>
    <p>{pending ? '新的作答正在进入地图。完成前，先不展示旧判断。' : hasScope ? '对应教材的知识点目录尚未就绪。拍照检查可以继续，目录校对完成后再呈现真实知识关系。' : '选好当前年级。以后每次有效的作答和订正，都会成为了解自己的依据。'}</p>
    <small>没有记录，不等于不会；也不需要为填满地图额外做题。</small></div>
}

/** Current-student nodes and actual prerequisite edges only. No inferred mastery. */
export function KnowledgeMapView({ map, onOpen, selectedId, onOpenHomework }: { map: KnowledgeMap; onOpen: (knowledgeId: string) => void; selectedId?: string | null; onOpenHomework?: OpenHomework }) {
  const [subject, setSubject] = useState('all'), [chapterId, setChapterId] = useState('all'), [view, setView] = useState<'map' | 'list'>('map')
  const nodes = useMemo(() => knowledgeNodes(map), [map])
  const subjects = [...new Set(nodes.map(node => node.subject).filter((value): value is string => !!value))]
  const activeSubject = subjects.includes(subject) ? subject : 'all'
  const scoped = nodes.filter(node => activeSubject === 'all' || node.subject === activeSubject)
  const chapters = knowledgeChapters(scoped), activeChapter = chapters.find(chapter => chapter.id === chapterId)
  const visible = activeChapter?.nodes ?? scoped, recorded = nodes.filter(node => node.valid_evidence_count > 0).length, pending = map.projection.pending
  return <div className="knowledge-map-overview">
    <MappingCoverageNote coverage={map.mapping_coverage} onOpenHomework={onOpenHomework} />
    {pending && <p className="knowledge-pending" role="status"><RefreshCw size={17} /> {PENDING_NOTICE}</p>}
    {!nodes.length ? <MapEmpty pending={pending} hasScope={!!map.grade} /> : <>
      <div className="knowledge-map-intro"><div><span className="knowledge-folio"><Leaf size={16} /> 我的学习足迹</span><h2>看见一点点，走稳每一步。</h2><p>选择一个知识点，看看它和哪次作答有关。</p></div><div className="knowledge-map-tally">{pending ? <><Clock3 size={24} /><span>更新中</span></> : <><strong>{recorded}<small> / {nodes.length}</small></strong><span>个知识点有作答记录</span></>}</div></div>
      {subjects.length > 1 && <div className="knowledge-subject-tabs" aria-label="按学科查看"><button aria-pressed={activeSubject === 'all'} onClick={() => { setSubject('all'); setChapterId('all') }}>全部学科</button>{subjects.map(name => <button key={name} aria-pressed={activeSubject === name} onClick={() => { setSubject(name); setChapterId('all') }}>{name}</button>)}</div>}
      <div className="knowledge-browser"><nav className="knowledge-chapters" aria-label="知识地图章节"><span className="eyebrow">章节索引</span>
        <button className={!activeChapter ? 'active' : ''} aria-current={!activeChapter ? 'true' : undefined} onClick={() => setChapterId('all')}><span className="chapter-index"><Compass size={17} /></span><span>全部知识点</span><small>{scoped.length}</small></button>
        {chapters.map((chapter, index) => <button key={chapter.id} className={activeChapter?.id === chapter.id ? 'active' : ''} aria-current={activeChapter?.id === chapter.id ? 'true' : undefined} onClick={() => setChapterId(chapter.id)}><span className="chapter-index">{String(index + 1).padStart(2, '0')}</span><span>{chapter.name}</span><small>{chapter.nodes.length}</small></button>)}
        <p className="knowledge-index-note">一条连线，表示学习时会用到的基础。<br />它不会替你下判断。</p></nav>
        <section className="knowledge-map-sheet" aria-label={activeChapter?.name || '全部知识点'}><div className="knowledge-sheet-toolbar"><div><h3>{activeChapter?.name || (activeSubject === 'all' ? '我的知识地图' : activeSubject)}</h3><span>{visible.length} 个知识点 · 点开看证据</span></div><div className="knowledge-view-switch" aria-label="展示方式"><button aria-label="地图视图" aria-pressed={view === 'map'} onClick={() => setView('map')}><MapIcon size={17} /></button><button aria-label="列表视图" aria-pressed={view === 'list'} onClick={() => setView('list')}><List size={18} /></button></div></div>
          {view === 'map' ? <KnowledgeGraph nodes={visible} edges={map.edges ?? []} pending={pending} selectedId={selectedId} onOpen={onOpen} />
            : <div className="knowledge-node-list">{visible.map(node => <button className={selectedId === node.id ? 'selected' : ''} key={node.id} onClick={() => onOpen(node.id)}><span className={`knowledge-list-dot ${pending ? 'pending' : node.learning_status}`} /><span><strong>{node.name}</strong><small>{pending ? '记录正在更新' : sourceLabel(node.assessment_source)}</small></span><span className={`status-pill ${pending ? 'neutral' : statusLabels[node.learning_status].tone}`}>{pending ? '正在更新' : statusLabels[node.learning_status].label}</span><ChevronRight size={16} /></button>)}</div>}
          <div className="knowledge-legend" aria-label="知识点状态说明">{pending ? <span><span className="legend-dot" />状态更新中，暂不显示旧结论</span> : <><span><span className="legend-dot stable_for_now" />阶段性较稳</span><span><span className="legend-dot consolidating" />正在巩固</span><span><span className="legend-dot needs_help_now" />需要再看看</span><span><span className="legend-dot" />还不判断</span></>}</div>
        </section></div>
    </>}
    {(map.source_note || map.graph_review_status === 'source_verified_ai_reviewed') && <p className="knowledge-graph-origin"><BookOpen size={15} /><span>{map.book_identity?.review_label || (map.graph_review_status === 'source_verified_ai_reviewed' ? '目录来源已核对，知识点关系由 AI 整理' : '知识点目录来源')}{map.source_note ? `：${map.source_note}` : '；尚未经老师逐点核实。'}</span></p>}
    {!!map.subjects?.length && <details className="knowledge-curriculum"><summary><BookOpen size={16} /><span>当前教材范围</span><small>查看版本信息</small></summary><div>{map.subjects.map(item => <article key={item.subject}><strong>{item.subject} · {item.publisher_name}</strong><span>{item.books.map(book => book.title).join('、') || '教材目录待确认'}</span><small>{item.graph_ready ? '知识点目录已就绪' : '知识点目录仍在校对'}{item.catalog_verified_at ? ` · 目录核对 ${dayLabel(item.catalog_verified_at)}` : ''}</small></article>)}</div></details>}
    <p className="muted small knowledge-notice">{map.notice}</p>
  </div>
}

export function KnowledgeMapPage({ workspace, map, loading, loadError, onRefresh, onOpenHomework }: {
  workspace: Workspace; map: KnowledgeMap | null; loading: boolean; loadError: string; onRefresh: () => void; onOpenHomework?: OpenHomework
}) {
  const [openId, setOpenId] = useState<string | null>(null)
  const [detailState, setDetailState] = useState<{ key: string; value: KnowledgeDetail } | null>(null)
  const [errorState, setErrorState] = useState<{ key: string; message: string } | null>(null)
  const trigger = useRef<Element | null>(null)
  const { client, studentId, recordVersion } = workspace
  const requestKey = [studentId, openId, recordVersion, map?.grade, map?.graph_version, map?.projection.evidence_revision, map?.projection.state_version, map?.projection.pending].join('|')
  useEffect(() => {
    if (!openId || !studentId || loading || !map) return
    let live = true
    client.knowledgeNode(openId).then(value => { if (live) { setDetailState({ key: requestKey, value }); setErrorState(null) } }, (e: unknown) => { if (live) { setErrorState({ key: requestKey, message: safeError(e).message }); workspace.report(e) } })
    return () => { live = false }
  }, [openId, studentId, client, requestKey, loading, !!map])
  useEffect(() => { setOpenId(null); setDetailState(null); setErrorState(null) }, [studentId])
  const open = (id: string) => { if (!openId) trigger.current = document.activeElement; setOpenId(id) }
  const close = () => { setOpenId(null); if (trigger.current?.isConnected && 'focus' in trigger.current) (trigger.current as HTMLElement).focus() }
  const detail = detailState?.key === requestKey ? detailState.value : null, error = errorState?.key === requestKey ? errorState.message : null
  return <section className="knowledge-page"><div className="page-title-row"><div><span className="eyebrow">从一页作业，看见自己的进步</span><h1>我的知识地图</h1><p>每一个记录，都能回到真实的作答。</p></div><button className="button secondary knowledge-refresh" disabled={loading} onClick={onRefresh}><RefreshCw size={17} className={loading ? 'spin' : ''} /> 刷新</button></div>
    <div className="knowledge-context-bar"><div className="curriculum-grade"><label htmlFor="curriculum-grade-select">当前年级</label><select id="curriculum-grade-select" value={map?.grade ?? ''} disabled={!!workspace.busy || loading} onChange={event => { const grade = Number(event.target.value); if (grade) { setOpenId(null); void workspace.setStudentGrade(grade).then(onRefresh) } }}><option value="">请选择</option>{[5, 6, 7, 8].map(grade => <option value={grade} key={grade}>{grade <= 6 ? `小学${grade === 5 ? '五' : '六'}年级` : `初${grade - 6}`}</option>)}</select></div><span><CircleDot size={14} />{loading ? '正在同步' : map?.projection.pending ? '正在更新' : map?.projection.projected_at ? `最近整理 ${dayLabel(map.projection.projected_at)}` : '从有效作答开始记录'}</span></div>
    <CurriculumBookPicker workspace={workspace} grade={map?.grade} onChanged={() => { setOpenId(null); onRefresh() }} />
    {loading ? <div className="knowledge-loading" role="status"><LoaderCircle className="spin" size={26} /><h2>正在打开你的知识地图</h2><p>读取当前教材范围与最新作答记录…</p></div>
      : !map ? <div className="knowledge-load-error" role="alert"><BookOpen size={28} /><h2>这次没有读到知识地图</h2><p>{loadError || '稍后再试，已有作答记录会保留。'}</p><button className="button secondary" onClick={onRefresh}><RefreshCw size={16} /> 再试一次</button></div>
        : <div className={`knowledge-layout ${openId ? 'has-detail' : ''}`}><KnowledgeMapView map={map} onOpen={open} selectedId={openId} onOpenHomework={onOpenHomework} />{openId && <aside className="knowledge-detail-panel" aria-label="所选知识点详情">{error ? <div className="knowledge-load-error" role="alert"><p>{error}</p><button className="text-button" onClick={close}><ArrowLeft size={16} /> 回到地图</button></div> : detail ? <KnowledgeDetailView detail={detail} onBack={close} pending={map.projection.pending} onOpenNode={open} onOpenHomework={onOpenHomework} busy={!!workspace.busy} /> : <div className="knowledge-detail-loading" role="status"><LoaderCircle className="spin" size={22} /><p>正在打开这个知识点…</p><button className="text-button" onClick={close}>回到地图</button></div>}</aside>}</div>}
  </section>
}
