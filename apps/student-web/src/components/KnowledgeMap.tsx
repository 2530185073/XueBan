import { StarMap } from './StarMap'
import { ObservatoryTour } from './ObservatoryTour'
import { evidenceTarget, type EvidenceTarget } from '../core/evidenceTrace'
import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { ArrowLeft, ArrowUpRight, BookOpen, ChevronRight, CircleDot, Clock3, Compass, Leaf, List, LoaderCircle, Map as MapIcon, Presentation, RefreshCw, X } from 'lucide-react'
import { safeError } from '../core'
import { CurriculumBookPicker } from './CurriculumBookPicker'
import type { CurriculumSubject, EvidenceSources, KnowledgeDetail, KnowledgeMap, MappingCoverage } from '../core/learning'
import type { Workspace } from '../useStudentWorkspace'
import { sourceLabel, assistanceLabel, dayLabel, eventLabel, knowledgeChapters, knowledgeNodes, PENDING_NOTICE, reasonText, resultLabels, statusLabels } from '../learning'
import { isKnowledgeDemoEnabled } from '../dev/knowledgeDemoGate'

const KnowledgeDemoPage = import.meta.env.DEV ? lazy(() => import('../dev/KnowledgeDemoPage')) : null

type OpenHomework = (homeworkId: string, caseId: string, evidence?: EvidenceTarget) => void

const mappingStateText = { no_direct_match: '暂未找到直接对应的教材知识点', mapping_pending: '知识点归类尚未完成', not_linked: '尚未形成有效的知识点关联' }

type PracticeFeedbackItem = {
  response_id: string; session_id: string; item_id: string; case_id: string; homework_id: string
  kind: 'first_unassisted' | 'assisted' | 'retry'; result: 'correct' | 'incorrect'
  hint_used: boolean; occurred_at: string; stem_preview: string
}
type SubjectFeedback = {
  homework_feedback: { model_count: number; staff_count: number; pending_count: number }
  practice_feedback: {
    total: number; first_unassisted_count: number; first_unassisted_correct: number; first_unassisted_incorrect: number
    assisted_count: number; retry_count: number; items: PracticeFeedbackItem[]; truncated: boolean
  }
}

// Optional additive API fields are validated at this consumer. Older maps
// retain their existing subject statistics without inventing practice data.
function subjectFeedback(subject: CurriculumSubject): SubjectFeedback | null {
  const record = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value)
  const count = (value: unknown): value is number => Number.isSafeInteger(value) && (value as number) >= 0
  const h: unknown = 'homework_feedback' in subject ? subject.homework_feedback : null
  const p: unknown = 'practice_feedback' in subject ? subject.practice_feedback : null
  if (!record(h) || !record(p) || !['model_count', 'staff_count', 'pending_count'].every(key => count(h[key]))
    || !['total', 'first_unassisted_count', 'first_unassisted_correct', 'first_unassisted_incorrect', 'assisted_count', 'retry_count'].every(key => count(p[key]))
    || !Array.isArray(p.items) || p.items.length > 20 || typeof p.truncated !== 'boolean') return null
  if (!p.items.every((item: unknown) => record(item)
    && ['response_id', 'session_id', 'item_id', 'case_id', 'homework_id'].every(key => typeof item[key] === 'string' && !!item[key] && item[key].length <= 200)
    && ['first_unassisted', 'assisted', 'retry'].includes(String(item.kind)) && ['correct', 'incorrect'].includes(String(item.result))
    && typeof item.hint_used === 'boolean' && typeof item.occurred_at === 'string' && Number.isFinite(Date.parse(item.occurred_at))
    && typeof item.stem_preview === 'string' && item.stem_preview.length <= 180)) return null
  const feedback = { homework_feedback: h, practice_feedback: p } as SubjectFeedback
  const practice = feedback.practice_feedback, homework = feedback.homework_feedback
  if (homework.model_count + homework.staff_count !== subject.scorable_count
    || practice.total !== practice.first_unassisted_count + practice.assisted_count + practice.retry_count
    || practice.first_unassisted_count !== practice.first_unassisted_correct + practice.first_unassisted_incorrect
    || practice.items.length !== Math.min(practice.total, 20) || practice.truncated !== (practice.total > 20)
    || new Set(practice.items.map(item => item.response_id)).size !== practice.items.length) return null
  return feedback
}

export function SubjectFeedbackNote({ subjects, onOpenHomework }: { subjects?: CurriculumSubject[]; onOpenHomework?: OpenHomework }) {
  const rows = (subjects ?? []).map(subject => ({ subject, feedback: subjectFeedback(subject) }))
    .filter(({ subject, feedback }) => subject.scorable_count || feedback?.homework_feedback.pending_count || feedback?.practice_feedback.total)
  if (!rows.length) return null
  return <section className="knowledge-mapping-gap" aria-label="作答与练习参考记录">
    <div><span className="eyebrow">已经留下的学习足迹</span><h3>作业和练习，都有记录</h3>
      <p>以下是当前教材范围内的作答样本。AI 判定可能需要复核，样本参考分不代表知识掌握率。</p></div>
    {rows.map(({ subject, feedback }) => <article key={subject.subject}>
      <h3>{subject.subject} · {subject.publisher_name}</h3>
      <p>作业：{subject.scorable_count} 道有可用判定，正确 {subject.correct_count} 道，部分正确 {subject.partial_count} 道，错误 {Math.max(0, subject.incorrect_count - subject.partial_count)} 道。
        {subject.score_percent !== null && <>样本参考分 {subject.score_percent}。</>}</p>
      {feedback && <p className="small muted">其中 AI 初判 {feedback.homework_feedback.model_count} 道，老师核实 {feedback.homework_feedback.staff_count} 道；另有 {feedback.homework_feedback.pending_count} 道待核实，暂不计入参考分。</p>}
      {!subject.graph_ready && <p>对应教材的知识点目录尚未完成审核；这些作答先保留为参考记录，知识点关联与掌握状态待确认。</p>}
      {!!feedback?.practice_feedback.total && <>
        <p>已核验变式作答 {feedback.practice_feedback.total} 次：首次且未记录提示或答案帮助 {feedback.practice_feedback.first_unassisted_count} 次（正确 {feedback.practice_feedback.first_unassisted_correct} 次，错误 {feedback.practice_feedback.first_unassisted_incorrect} 次）；帮助后首次作答 {feedback.practice_feedback.assisted_count} 次；同题重做 {feedback.practice_feedback.retry_count} 次。</p>
        <details><summary>查看练习记录</summary><ul>{feedback.practice_feedback.items.map(item => <li key={item.response_id}>
          <span><span className="mapping-question">{item.stem_preview}</span><small>{dayLabel(item.occurred_at)} · {item.kind === 'retry' ? '同题重做' : item.kind === 'assisted' ? '提示或答案后作答' : '首次作答，未记录应用内帮助'} · {item.result === 'correct' ? '本次做对' : '本次仍需练习'}</small></span>
          {onOpenHomework && <button className="text-button" onClick={() => onOpenHomework(item.homework_id, item.case_id)}>回看来源原题 <ArrowUpRight size={15} /></button>}
        </li>)}</ul>{feedback.practice_feedback.truncated && <p className="small muted">这里展示最近 20 次，统计包含全部符合范围的作答。</p>}</details>
        <p className="small muted">题目与答案已通过 AI 核验，尚未经老师核实。未记录帮助不等于已确认独立完成；看答案后做对或同题重做，不直接升级为掌握。</p>
      </>}
    </article>)}
  </section>
}

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

export function KnowledgeDetailView({ detail, onBack, pending = false, onOpenNode, onOpenHomework, busy = false, children }: {
  detail: KnowledgeDetail; onBack: () => void; pending?: boolean
  onOpenNode?: (id: string) => void; onOpenHomework?: OpenHomework; busy?: boolean; children?: ReactNode
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
    {children}
    <div className="knowledge-block"><div className="knowledge-section-heading"><h3>从作答中看见的变化</h3>{!updating && <span>{evidence.length} 条记录</span>}</div>
      {updating ? <div className="knowledge-detail-empty" role="status"><RefreshCw size={22} /><p>正在整理新的作答记录</p><small>完成后再看这次变化。</small></div>
        : evidence.length ? <ol className="evidence-list">{evidence.map(e => <li key={e.id}>
          <div className="evidence-meta"><time dateTime={e.occurred_at}>{dayLabel(e.occurred_at)}</time><span>{eventLabel(e.event_type)}</span><span className={`evidence-source ${e.source_role === 'sitting_teacher' ? 'staff' : ''}`}>{e.source_role === 'model' ? 'AI 初判' : e.source_role === 'sitting_teacher' ? '老师核实' : '来源待核实'}</span></div>
          {e.homework_title && <p className="evidence-homework">{e.homework_title}</p>}
          <p className="evidence-question">{e.question_text || '这条记录暂未附题目文字。'}</p>
          {e.answer_text && <p className="evidence-question"><strong>学生作答：</strong>{e.answer_text}</p>}
          {e.parent_attempt_id && <small className="evidence-parent">这次订正已关联原错误作答，原题与父链均保留。</small>}
          <div className="evidence-tags"><span className="status-pill neutral">{resultLabels[e.result]}</span><span className="muted small">{assistanceLabel(e.assistance_level)}</span></div>
          {e.homework_id && e.case_id && onOpenHomework && <button className="text-button evidence-open" disabled={busy} onClick={() => onOpenHomework(e.homework_id!, e.case_id!, evidenceTarget(detail, e))}>查看原题与订正 <ArrowUpRight size={15} /></button>}
        </li>)}</ol>
        : <div className="knowledge-detail-empty"><Leaf size={25} strokeWidth={1.4} /><p>这里还没有可用的作答记录</p><small>以后遇到相关学校作业，再慢慢补全。</small></div>}
    </div>
    {detail.prerequisites.length > 0 && <div className="knowledge-block"><h3>会用到的基础</h3><ul className="prerequisite-list">{detail.prerequisites.map(p => <li key={p.id}>
      {onOpenNode ? <button onClick={() => onOpenNode(p.id)}><span>{p.name}</span><ChevronRight size={15} /></button> : <span>{p.name}</span>}
      {!updating && <span className={`status-pill ${statusLabels[p.learning_status].tone}`}>{statusLabels[p.learning_status].label}</span>}
    </li>)}</ul><p className="muted small">有关联，不代表这些知识点也有困难。</p></div>}
    {!!detail.leads_to?.length && <div className="knowledge-block"><h3>会继续用在</h3><ul className="prerequisite-list">{detail.leads_to.map(p => <li key={p.id}>
      {onOpenNode ? <button onClick={() => onOpenNode(p.id)}><span>{p.name}</span><ChevronRight size={15} /></button> : <span>{p.name}</span>}
      {!updating && <span className={`status-pill ${statusLabels[p.learning_status].tone}`}>{statusLabels[p.learning_status].label}</span>}
    </li>)}</ul><p className="muted small">后续关联只表示学习上的联系，不自动推断掌握。</p></div>}
    <p className="muted small knowledge-notice">{detail.notice}</p>
    <button className="text-button knowledge-detail-back" onClick={onBack}><ArrowLeft size={16} /> 回到知识地图</button>
  </section>
}

const graphUnavailableText = {
  missing_grade: '还未登记当前年级，请先选择年级，才能核对对应教材范围。',
  catalog_unavailable: '当前教材目录来源尚未核对完整，暂不能提供知识点图谱。已有作答会保留为参考记录。',
  no_reviewed_graph: '当前年级与教材范围内没有已完成审核的知识图谱。已有作答会保留为参考记录，暂不生成知识点或掌握状态。',
  no_matching_nodes: '已发布图谱中没有与当前教材范围匹配的知识点。已有作答会保留为参考记录，待教材范围核对完成。',
}

function MapEmpty({ pending, hasScope, reason }: { pending: boolean; hasScope: boolean; reason?: keyof typeof graphUnavailableText }) {
  return <div className="knowledge-empty"><div className="knowledge-empty-symbol"><BookOpen size={35} strokeWidth={1.3} /><span><Leaf size={17} /></span></div><span className="eyebrow">每个人，都有自己的节奏</span>
    <h2>{pending ? '正在整理你的学习足迹' : hasScope ? '这张地图，还在慢慢准备' : '从自己的学校作业开始'}</h2>
    <p>{reason ? graphUnavailableText[reason] : pending ? '新的作答正在进入地图。完成前，先不展示旧判断。' : hasScope ? '对应教材的知识点目录尚未就绪。拍照检查可以继续，目录校对完成后再呈现真实知识关系。' : '选好当前年级。以后每次有效的作答和订正，都会成为了解自己的依据。'}</p>
    <small>没有记录，不等于不会；也不需要为填满地图额外做题。</small></div>
}

/** Current-student nodes and actual prerequisite edges only. No inferred mastery. */
export function KnowledgeMapView({ map, onOpen, selectedId, onOpenHomework, detail = null, busy = false }: {
  map: KnowledgeMap; onOpen: (knowledgeId: string) => void; selectedId?: string | null; onOpenHomework?: OpenHomework
  detail?: KnowledgeDetail | null; busy?: boolean
}) {
  const [subject, setSubject] = useState('all'), [chapterId, setChapterId] = useState('all'), [view, setView] = useState<'map' | 'list'>('map')
  const [presenting, setPresenting] = useState(false)
  const closePresentation = useCallback(() => setPresenting(false), [])
  const nodes = useMemo(() => knowledgeNodes(map), [map])
  const subjects = [...new Set(nodes.map(node => node.subject).filter((value): value is string => !!value))]
  const activeSubject = subjects.includes(subject) ? subject : 'all'
  const scoped = nodes.filter(node => activeSubject === 'all' || node.subject === activeSubject)
  const chapters = knowledgeChapters(scoped), activeChapter = chapters.find(chapter => chapter.id === chapterId)
  const visible = activeChapter?.nodes ?? scoped, recorded = nodes.filter(node => node.valid_evidence_count > 0).length, pending = !!nodes.length && map.projection.pending
  const unavailable = 'graph_unavailable_reason' in map ? map.graph_unavailable_reason : undefined
  const reason = typeof unavailable === 'string' && Object.hasOwn(graphUnavailableText, unavailable) ? unavailable as keyof typeof graphUnavailableText : undefined
  return <div className="knowledge-map-overview">
    <SubjectFeedbackNote subjects={map.subjects} onOpenHomework={onOpenHomework} />
    <MappingCoverageNote coverage={map.mapping_coverage} onOpenHomework={onOpenHomework} />
    {pending && <p className="knowledge-pending" role="status"><RefreshCw size={17} /> {PENDING_NOTICE}</p>}
    {!nodes.length ? <MapEmpty pending={pending} hasScope={!!map.grade} reason={reason} /> : <>
      <div className="knowledge-map-intro"><div><span className="knowledge-folio"><Leaf size={16} /> 我的学习足迹</span><h2>看见一点点，走稳每一步。</h2><p>选择一个知识点，看看它和哪次作答有关。</p></div><div className="knowledge-map-tally">{pending ? <><Clock3 size={24} /><span>更新中</span></> : <><strong>{recorded}<small> / {nodes.length}</small></strong><span>个知识点有作答记录</span></>}</div></div>
      {subjects.length > 1 && <div className="knowledge-subject-tabs" aria-label="按学科查看"><button aria-pressed={activeSubject === 'all'} onClick={() => { setSubject('all'); setChapterId('all') }}>全部学科</button>{subjects.map(name => <button key={name} aria-pressed={activeSubject === name} onClick={() => { setSubject(name); setChapterId('all') }}>{name}</button>)}</div>}
      <div className={`knowledge-browser ${view === 'map' ? 'star' : ''}`}><nav className="knowledge-chapters" aria-label="知识地图章节"><span className="eyebrow">章节索引</span>
        <button className={!activeChapter ? 'active' : ''} aria-current={!activeChapter ? 'true' : undefined} onClick={() => setChapterId('all')}><span className="chapter-index"><Compass size={17} /></span><span>全部知识点</span><small>{scoped.length}</small></button>
        {chapters.map((chapter, index) => <button key={chapter.id} className={activeChapter?.id === chapter.id ? 'active' : ''} aria-current={activeChapter?.id === chapter.id ? 'true' : undefined} onClick={() => setChapterId(chapter.id)}><span className="chapter-index">{String(index + 1).padStart(2, '0')}</span><span>{chapter.name}</span><small>{chapter.nodes.length}</small></button>)}
        <p className="knowledge-index-note">一条连线，表示学习时会用到的基础。<br />它不会替你下判断。</p></nav>
        <section className={`knowledge-map-sheet ${view === 'map' ? 'star' : ''}`} aria-label={activeChapter?.name || '全部知识点'}><div className="knowledge-sheet-toolbar"><div><h3>{activeChapter?.name || (activeSubject === 'all' ? '我的知识地图' : activeSubject)}</h3><span>{visible.length} 个知识点 · 点开看证据</span></div><div className="knowledge-sheet-actions">{view === 'map' && <button className="observatory-launch" disabled={pending} onClick={() => setPresenting(true)}><Presentation size={16} /><span>展示模式</span></button>}<div className="knowledge-view-switch" aria-label="展示方式"><button aria-label="地图视图" aria-pressed={view === 'map'} onClick={() => setView('map')}><MapIcon size={17} /></button><button aria-label="列表视图" aria-pressed={view === 'list'} onClick={() => setView('list')}><List size={18} /></button></div></div></div>
          {view === 'map' ? <StarMap nodes={visible} edges={map.edges ?? []} pending={pending} selectedId={selectedId} onOpen={onOpen} />
            : <div className="knowledge-node-list">{visible.map(node => <button className={selectedId === node.id ? 'selected' : ''} key={node.id} onClick={() => onOpen(node.id)}><span className={`knowledge-list-dot ${pending ? 'pending' : node.learning_status}`} /><span><strong>{node.name}</strong><small>{pending ? '记录正在更新' : sourceLabel(node.assessment_source)}</small></span><span className={`status-pill ${pending ? 'neutral' : statusLabels[node.learning_status].tone}`}>{pending ? '正在更新' : statusLabels[node.learning_status].label}</span><ChevronRight size={16} /></button>)}</div>}
          {view === 'map' ? <div className="star-legend" aria-label="知识点状态说明">{pending ? <span><i className="none" />状态更新中，暂不显示旧结论</span> : <><span><i className="stable_for_now" />阶段性较稳</span><span><i className="consolidating" />正在巩固</span><span><i className="needs_help_now" />需要再看看</span><span><i className="none" />还不判断</span></>}<small>亮度表示记录的多少与来源，不是分数</small></div> : <div className="knowledge-legend" aria-label="知识点状态说明">{pending ? <span><span className="legend-dot" />状态更新中，暂不显示旧结论</span> : <><span><span className="legend-dot stable_for_now" />阶段性较稳</span><span><span className="legend-dot consolidating" />正在巩固</span><span><span className="legend-dot needs_help_now" />需要再看看</span><span><span className="legend-dot" />还不判断</span></>}</div>}
        </section></div>
    </>}
    {(map.source_note || map.graph_review_status === 'source_verified_ai_reviewed') && <p className="knowledge-graph-origin"><BookOpen size={15} /><span>{map.book_identity?.review_label || (map.graph_review_status === 'source_verified_ai_reviewed' ? '目录来源已核对，知识点关系由 AI 整理' : '知识点目录来源')}{map.source_note ? `：${map.source_note}` : '；尚未经老师逐点核实。'}</span></p>}
    {!!map.subjects?.length && <details className="knowledge-curriculum"><summary><BookOpen size={16} /><span>当前教材范围</span><small>查看版本信息</small></summary><div>{map.subjects.map(item => <article key={item.subject}><strong>{item.subject} · {item.publisher_name}</strong><span>{item.books.map(book => book.title).join('、') || '教材目录待确认'}</span><small>{item.graph_ready ? '知识点目录已就绪' : '知识点目录仍在校对'}{item.catalog_verified_at ? ` · 目录核对 ${dayLabel(item.catalog_verified_at)}` : ''}</small></article>)}</div></details>}
    <p className="muted small knowledge-notice">{map.notice}</p>
    {presenting && createPortal(<ObservatoryTour nodes={scoped} edges={map.edges ?? []} pending={pending} detail={detail} busy={busy} onOpen={onOpen} onOpenHomework={onOpenHomework} onClose={closePresentation} />, document.body)}
  </div>
}

type KnowledgeMapPageProps = {
  workspace: Workspace; map: KnowledgeMap | null; loading: boolean; loadError: string; onRefresh: () => void; onOpenHomework?: OpenHomework
}

/** Local, explicitly requested preview; it never replaces API data or writes learning records. */
export function KnowledgeMapPage(props: KnowledgeMapPageProps) {
  const [dismissed, setDismissed] = useState(false)
  const studentId = props.workspace.studentId
  const enabled = import.meta.env.DEV && typeof window !== 'undefined' && !dismissed && !props.loading &&
    props.map?.student_id === studentId && isKnowledgeDemoEnabled({
      development: import.meta.env.DEV, hostname: window.location.hostname,
      requestedStudent: new URLSearchParams(window.location.search).get('knowledgeDemo'),
      currentStudent: studentId, grade: props.map?.grade,
    })
  if (enabled && KnowledgeDemoPage) return <Suspense fallback={<p role="status">正在打开模拟图谱…</p>}>
    <KnowledgeDemoPage key={studentId} onExit={() => {
      const url = new URL(window.location.href)
      url.searchParams.delete('knowledgeDemo')
      window.history.replaceState(window.history.state, '', url)
      setDismissed(true)
    }} />
  </Suspense>
  return <LiveKnowledgeMapPage {...props} />
}

function LiveKnowledgeMapPage({ workspace, map, loading, loadError, onRefresh, onOpenHomework }: KnowledgeMapPageProps) {
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
    <CurriculumBookPicker workspace={workspace} grade={map?.grade} onChanged={() => { setOpenId(null); void workspace.refreshCurriculum(); onRefresh() }} />
    {loading ? <div className="knowledge-loading" role="status"><LoaderCircle className="spin" size={26} /><h2>正在打开你的知识地图</h2><p>读取当前教材范围与最新作答记录…</p></div>
      : !map ? <div className="knowledge-load-error" role="alert"><BookOpen size={28} /><h2>这次没有读到知识地图</h2><p>{loadError || '稍后再试，已有作答记录会保留。'}</p><button className="button secondary" onClick={onRefresh}><RefreshCw size={16} /> 再试一次</button></div>
        : <div className={`knowledge-layout ${openId ? 'has-detail' : ''}`}><KnowledgeMapView map={map} onOpen={open} selectedId={openId} onOpenHomework={onOpenHomework} detail={detail} busy={!!workspace.busy} />{openId && <aside className="knowledge-detail-panel" aria-label="所选知识点详情">{error ? <div className="knowledge-load-error" role="alert"><p>{error}</p><button className="text-button" onClick={close}><ArrowLeft size={16} /> 回到地图</button></div> : detail ? <KnowledgeDetailView detail={detail} onBack={close} pending={map.projection.pending} onOpenNode={open} onOpenHomework={onOpenHomework} busy={!!workspace.busy} /> : <div className="knowledge-detail-loading" role="status"><LoaderCircle className="spin" size={22} /><p>正在打开这个知识点…</p><button className="text-button" onClick={close}>回到地图</button></div>}</aside>}</div>}
  </section>
}
