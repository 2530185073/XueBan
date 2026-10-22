import { Galaxy } from './Galaxy'
import { evidenceTarget, type EvidenceTarget } from '../core/evidenceTrace'
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { ArrowLeft, ArrowUpRight, BookOpen, ChevronRight, CircleDot, Clock3, Compass, Leaf, List, LoaderCircle, Map as MapIcon, RefreshCw, X } from 'lucide-react'
import { safeError } from '../core'
import { CurriculumBookPicker } from './CurriculumBookPicker'
import type { EvidenceSources, KnowledgeDetail, KnowledgeMap, MappingCoverage } from '../core/learning'
import type { Workspace } from '../useStudentWorkspace'
import { sourceLabel, assistanceLabel, dayLabel, eventLabel, knowledgeChapters, knowledgeNodes, PENDING_NOTICE, reasonText, resultLabels, statusLabels } from '../learning'

type OpenHomework = (homeworkId: string, caseId: string, evidence?: EvidenceTarget) => void

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
          {e.homework_id && e.case_id && onOpenHomework && <button className="text-button evidence-open" disabled={busy} onClick={() => onOpenHomework(e.homework_id!, e.case_id!, evidenceTarget(detail, e))}>查看原题与订正 <ArrowUpRight size={15} /></button>}
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

function MapEmpty({ pending, hasScope }: { pending: boolean; hasScope: boolean }) {
  return <div className="knowledge-empty"><div className="knowledge-empty-symbol"><BookOpen size={35} strokeWidth={1.3} /><span><Leaf size={17} /></span></div><span className="eyebrow">每个人，都有自己的节奏</span>
    <h2>{pending ? '正在整理你的学习足迹' : hasScope ? '这张地图，还在慢慢准备' : '从自己的学校作业开始'}</h2>
    <p>{pending ? '新的作答正在进入地图。完成前，先不展示旧判断。' : hasScope ? '对应教材的知识点目录尚未就绪。拍照检查可以继续，目录校对完成后再呈现真实知识关系。' : '选好当前年级。以后每次有效的作答和订正，都会成为了解自己的依据。'}</p>
    <small>没有记录，不等于不会；也不需要为填满地图额外做题。</small></div>
}

const FILTERS = [
  { key: 'all', label: '全部' }, { key: 'needs_help_now', label: '需要再看看' }, { key: 'consolidating', label: '正在巩固' },
  { key: 'stable_for_now', label: '阶段性较稳' }, { key: 'no_judgment', label: '还不判断' },
] as const
type Filter = (typeof FILTERS)[number]['key']

/**
 * The map is the page: a full-bleed stage with a floating HUD. Chapters are chips that fly the camera to a cluster, status
 * filters dim everything else, and a selected star opens the evidence in a drawer beside the map instead of far below it.
 * Position, size and glow carry no score: the same four plain-word statuses as everywhere else.
 */
export function KnowledgeMapView({ map, onOpen, selectedId, onOpenHomework, drawer, onCloseDrawer }: {
  map: KnowledgeMap; onOpen: (knowledgeId: string) => void; selectedId?: string | null; onOpenHomework?: OpenHomework
  drawer?: React.ReactNode; onCloseDrawer?: () => void
}) {
  const [chapterId, setChapterId] = useState<string | null>(null), [view, setView] = useState<'map' | 'list'>('map'), [filter, setFilter] = useState<Filter>('all')
  const nodes = useMemo(() => knowledgeNodes(map), [map])
  const chapters = useMemo(() => knowledgeChapters(nodes), [nodes])
  const pending = map.projection.pending
  const counts = useMemo(() => {
    const c: Record<Filter, number> = { all: nodes.length, needs_help_now: 0, consolidating: 0, stable_for_now: 0, no_judgment: 0 }
    for (const n of nodes) c[n.learning_status]++
    return c
  }, [nodes])
  const shown = filter === 'all' || pending ? nodes : nodes.filter(n => n.learning_status === filter)
  const recorded = nodes.filter(n => n.valid_evidence_count > 0).length
  const stage = useRef<HTMLDivElement>(null)
  const [drawerWidth, setDrawerWidth] = useState(0)
  useLayoutEffect(() => {
    if (!drawer || !stage.current) { setDrawerWidth(0); return }
    const width = stage.current.getBoundingClientRect().width
    setDrawerWidth(width >= 900 ? Math.min(430, width * 0.4) : 0)
  }, [drawer])
  return <div className="knowledge-map-overview galaxy-page">
    <MappingCoverageNote coverage={map.mapping_coverage} onOpenHomework={onOpenHomework} />
    {pending && <p className="knowledge-pending" role="status"><RefreshCw size={17} /> {PENDING_NOTICE}</p>}
    {!nodes.length ? <MapEmpty pending={pending} hasScope={!!map.grade} /> : <div className={`galaxy-stage ${drawer ? 'has-drawer' : ''}`} ref={stage}>
      <header className="galaxy-hud top">
        <div className="galaxy-title"><span className="galaxy-eyebrow"><Leaf size={14} /> 我的学习足迹</span><h2>看见一点点，走稳每一步</h2></div>
        <div className="galaxy-tally">{pending ? <><Clock3 size={20} /><span>更新中</span></> : <><strong>{recorded}</strong><small> / {nodes.length}</small><span>个知识点有作答记录</span></>}</div>
        <div className="galaxy-view" role="group" aria-label="展示方式">
          <button aria-label="地图视图" aria-pressed={view === 'map'} onClick={() => setView('map')}><MapIcon size={18} /></button>
          <button aria-label="列表视图" aria-pressed={view === 'list'} onClick={() => setView('list')}><List size={19} /></button>
        </div>
      </header>
      {view === 'map'
        ? <Galaxy nodes={shown} edges={map.edges ?? []} pending={pending} selectedId={selectedId} focusChapterId={chapterId} onOpen={onOpen} coveredRight={drawerWidth} />
        : <div className="galaxy-list"><div className="knowledge-node-list">{shown.map(node => <button className={selectedId === node.id ? 'selected' : ''} key={node.id} onClick={() => onOpen(node.id)}><span className={`knowledge-list-dot ${pending ? 'pending' : node.learning_status}`} /><span><strong>{node.name}</strong><small>{pending ? '记录正在更新' : sourceLabel(node.assessment_source)}</small></span><span className={`status-pill ${pending ? 'neutral' : statusLabels[node.learning_status].tone}`}>{pending ? '正在更新' : statusLabels[node.learning_status].label}</span><ChevronRight size={16} /></button>)}</div></div>}
      <nav className="galaxy-hud chapters" aria-label="按章节飞行">
        <button className={chapterId === null ? 'active' : ''} aria-pressed={chapterId === null} onClick={() => setChapterId(null)}><Compass size={16} /> 全景</button>
        {chapters.map((c, i) => <button key={c.id} className={chapterId === c.id ? 'active' : ''} aria-pressed={chapterId === c.id} onClick={() => setChapterId(chapterId === c.id ? null : c.id)}><b>{String(i + 1).padStart(2, '0')}</b>{c.name}</button>)}
      </nav>
      {!pending && <div className="galaxy-hud filters" role="group" aria-label="按状态筛选">
        {FILTERS.map(f => <button key={f.key} className={`${f.key} ${filter === f.key ? 'active' : ''}`} aria-pressed={filter === f.key} onClick={() => setFilter(f.key)} disabled={f.key !== 'all' && !counts[f.key]}><i />{f.label}<small>{counts[f.key]}</small></button>)}
      </div>}
      <div className="galaxy-hud foot"><span>拖动移动 · 滚轮或双指缩放 · 点星星看证据</span><span>亮度表示记录的多少与来源，不是分数</span></div>
      {drawer && <aside className="galaxy-drawer" aria-label="所选知识点详情"><button className="galaxy-drawer-close" onClick={onCloseDrawer} aria-label="关闭详情并回到全景"><X size={18} /></button>{drawer}</aside>}
    </div>}
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
        : <KnowledgeMapView map={map} onOpen={open} selectedId={openId} onOpenHomework={onOpenHomework} onCloseDrawer={close}
            drawer={openId ? (error ? <div className="knowledge-load-error" role="alert"><p>{error}</p><button className="text-button" onClick={close}><ArrowLeft size={16} /> 回到地图</button></div> : detail ? <KnowledgeDetailView detail={detail} onBack={close} pending={map.projection.pending} onOpenNode={open} onOpenHomework={onOpenHomework} busy={!!workspace.busy} /> : <div className="knowledge-detail-loading" role="status"><LoaderCircle className="spin" size={22} /><p>正在打开这个知识点…</p><button className="text-button" onClick={close}>回到地图</button></div>) : undefined} />}
  </section>
}
