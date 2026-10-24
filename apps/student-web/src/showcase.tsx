import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { BookOpen, CalendarDays, CheckCircle2, Database, X } from 'lucide-react'
import { KnowledgeDetailView, KnowledgeMapView } from './components/KnowledgeMap'
import { SemesterNodeFacts, type SemesterNodeFact } from './components/SemesterNodeFacts'
import type { KnowledgeDetail, KnowledgeMap } from './core/learning'
import type { EvidenceTarget } from './core/evidenceTrace'
import { assistanceLabel, dayLabel, eventLabel, resultLabels } from './learning'
import semester from './core/showcaseSemester.json'
import '@fontsource-variable/noto-serif-sc/wght.css'
import '@fontsource-variable/noto-sans-sc/wght.css'
import './styles.css'
import './record.css'
import './learning.css'
import './design-system.css'

const map = semester.map as KnowledgeMap
const details = semester.details as Record<string, KnowledgeDetail>
const facts = semester.facts as Record<string, SemesterNodeFact>
const meta = semester.meta

function Showcase() {
  const [selected, setSelected] = useState<string | null>(null)
  const [previewId, setPreviewId] = useState<string | null>(null)
  const open = useCallback((id: string) => setSelected(id), [])
  const preview = useMemo(() => Object.values(details).flatMap(detail => detail.evidence).find(item => item.id === previewId) ?? null, [previewId])
  const openHomework = useCallback((_homeworkId: string, _caseId: string, target?: EvidenceTarget) => { if (target) setPreviewId(target.evidenceId) }, [])
  const detailPanel = useRef<HTMLElement>(null)
  const recordPanel = useRef<HTMLElement>(null)
  const recordClose = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    if (!selected || window.innerWidth > 1050) return
    const timer = window.setTimeout(() => detailPanel.current?.scrollIntoView({ behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'start' }), 120)
    return () => window.clearTimeout(timer)
  }, [selected])
  useEffect(() => {
    if (!previewId) return
    const previousOverflow = document.body.style.overflow, previousFocus = document.activeElement as HTMLElement | null
    document.body.style.overflow = 'hidden'; recordClose.current?.focus()
    const close = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); setPreviewId(null); return }
      if (event.key !== 'Tab') return
      const focusable = [...(recordPanel.current?.querySelectorAll<HTMLElement>('button:not(:disabled), a[href], input, [tabindex="0"]') ?? [])].filter(element => element.getClientRects().length)
      if (!focusable.length) return
      const first = focusable[0], last = focusable[focusable.length - 1]
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
    }
    document.addEventListener('keydown', close)
    return () => { document.body.style.overflow = previousOverflow; document.removeEventListener('keydown', close); if (previousFocus?.isConnected) previousFocus.focus() }
  }, [previewId])
  return <main className="knowledge-page semester-showcase" style={{ maxWidth: 1600, margin: '0 auto', padding: '28px' }}>
    <div className="page-title-row"><div><span className="eyebrow">{meta.period} · 完整学习档案</span><h1>我的知识地图</h1><p>{meta.source}</p></div></div>
    <section className="semester-provenance" aria-label="展示数据依据">
      <div className="semester-student"><span className="semester-student-mark"><BookOpen size={20} /></span><div><strong>{meta.studentLabel}</strong><p>{meta.dateRange} · 一学期学校作业、订正与复习记录</p></div></div>
      <dl><div><dt>教材知识点</dt><dd>{meta.skillCount}</dd></div><div><dt>作答与订正</dt><dd>{meta.evidenceCount}</dd></div><div><dt>订正父链</dt><dd>{meta.correctionCount}</dd></div><div><dt>阶段性较稳</dt><dd>{meta.statusCounts.stable_for_now}</dd></div></dl>
      <p className="semester-honesty"><Database size={14} />{meta.honesty}</p>
    </section>
    <div className={`semester-workspace ${selected ? 'has-detail' : ''}`}>
      <KnowledgeMapView map={map} selectedId={selected} onOpen={open} detail={selected ? details[selected] : null} onOpenHomework={openHomework} />
      {selected && <aside ref={detailPanel} className="semester-detail-panel" aria-label="所选知识点的完整学习档案"><KnowledgeDetailView detail={details[selected]} onBack={() => setSelected(null)} onOpenNode={open} onOpenHomework={openHomework}><SemesterNodeFacts facts={facts[selected]} /></KnowledgeDetailView></aside>}
    </div>
    {preview && <div className="semester-record-backdrop" onClick={() => setPreviewId(null)}><section ref={recordPanel} className="semester-record" role="dialog" aria-modal="true" aria-label="原题作答记录" onClick={event => event.stopPropagation()}>
      <button ref={recordClose} className="semester-record-close" onClick={() => setPreviewId(null)} aria-label="关闭原题记录"><X size={18} /></button>
      <span className="eyebrow">学校作业记录 · 演示档案</span><h2>{preview.homework_title}</h2>
      <div className="semester-record-meta"><span><CalendarDays size={14} />{dayLabel(preview.occurred_at)}</span><span>{eventLabel(preview.event_type)}</span><span>老师核实</span></div>
      <div className="semester-record-question"><small>原题</small><p>{preview.question_text}</p></div>
      <div className="semester-record-answer"><small>学生作答</small><p>{preview.answer_text}</p></div>
      <div className="semester-record-result"><CheckCircle2 size={17} /><strong>{resultLabels[preview.result]}</strong><span>{assistanceLabel(preview.assistance_level)}</span></div>
      {preview.parent_attempt_id && <p className="semester-record-parent">这次订正已关联原错误作答，父链与发生时间均保留。</p>}
      <p className="semester-record-notice">这里展示的是结构化原题与作答记录；没有伪造作业照片或真实学生身份。</p>
    </section></div>}
  </main>
}

createRoot(document.getElementById('root')!).render(<Showcase />)
