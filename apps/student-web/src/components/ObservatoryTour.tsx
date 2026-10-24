import { useEffect, useMemo, useRef, useState } from 'react'
import { ArrowLeft, ArrowRight, ArrowUpRight, BookOpen, LoaderCircle, Pause, Play, RotateCcw, X } from 'lucide-react'
import { createObservatoryPlan } from '../core/observatory'
import { evidenceTarget, type EvidenceTarget } from '../core/evidenceTrace'
import type { KnowledgeDetail, KnowledgeGraphEdge, KnowledgeGraphNode } from '../core/learning'
import { assistanceLabel, dayLabel, eventLabel, reasonText, resultLabels, sourceLabel, statusLabels } from '../learning'
import { StarMap } from './StarMap'

type OpenHomework = (homeworkId: string, caseId: string, evidence?: EvidenceTarget) => void
const STOP_NAMES = ['全局', '章节', '知识点', '关系', '证据']

export function ObservatoryTour({ nodes, edges, pending, detail, busy, onOpen, onOpenHomework, onClose }: {
  nodes: KnowledgeGraphNode[]; edges: KnowledgeGraphEdge[]; pending: boolean
  detail: KnowledgeDetail | null; busy?: boolean
  onOpen: (id: string) => void
  onOpenHomework?: OpenHomework
  onClose: () => void
}) {
  const plan = useMemo(() => createObservatoryPlan(nodes, edges), [nodes, edges])
  const root = useRef<HTMLDivElement>(null)
  const evidencePanel = useRef<HTMLElement>(null)
  const closeButton = useRef<HTMLButtonElement>(null)
  const [step, setStep] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [exploreId, setExploreId] = useState<string | null>(null)
  const activeId = exploreId ?? (step >= 2 ? plan?.focus.id ?? null : null)
  const activeNode = nodes.find(node => node.id === activeId) ?? plan?.focus ?? null
  const activeDetail = detail && activeNode && detail.node.id === activeNode.id ? detail : null
  const shownNodes = step === 0 || !plan ? nodes : plan.chapterNodes

  useEffect(() => {
    if (!plan) return
    const previousOverflow = document.body.style.overflow
    const previousFocus = document.activeElement as HTMLElement | null
    document.body.style.overflow = 'hidden'
    closeButton.current?.focus()
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); onClose(); return }
      const target = event.target as HTMLElement
      if (event.key === 'Tab') {
        const focusable = [...(root.current?.querySelectorAll<HTMLElement>('button:not(:disabled), input, [tabindex="0"]') ?? [])].filter(element => element.getClientRects().length)
        if (!focusable.length) return
        const first = focusable[0], last = focusable[focusable.length - 1]
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
        return
      }
      if (target.closest('input, textarea, select, button')) return
      if (event.key === 'ArrowRight') { event.preventDefault(); setPlaying(false); setStep(current => Math.min(4, current + 1)) }
      if (event.key === 'ArrowLeft') { event.preventDefault(); setPlaying(false); setStep(current => Math.max(0, current - 1)) }
      if (event.key === ' ') { event.preventDefault(); setPlaying(current => !current) }
    }
    document.addEventListener('keydown', onKey)
    return () => {
      document.body.style.overflow = previousOverflow
      document.removeEventListener('keydown', onKey)
      if (previousFocus?.isConnected) previousFocus.focus()
    }
  }, [onClose, plan])

  useEffect(() => {
    setExploreId(null)
    if (step >= 2 && plan) onOpen(plan.focus.id)
    if (step === 4 && window.innerWidth <= 900) {
      const timer = window.setTimeout(() => evidencePanel.current?.scrollIntoView({ behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'start' }), 180)
      return () => window.clearTimeout(timer)
    }
  }, [step, plan, onOpen])
  useEffect(() => {
    if (!playing || step >= 4) return
    const timer = window.setTimeout(() => {
      if (step === 3) setPlaying(false)
      setStep(current => Math.min(4, current + 1))
    }, step === 0 ? 5200 : 6800)
    return () => window.clearTimeout(timer)
  }, [playing, step])

  if (!plan || !activeNode) return null
  const go = (next: number) => { setPlaying(false); setStep(Math.max(0, Math.min(4, next))) }
  const explore = (id: string) => { setPlaying(false); setExploreId(id); setStep(2); onOpen(id) }
  const status = statusLabels[activeNode.learning_status]
  const copy = [
    { eyebrow: '全册总览', title: '每一次作答，都在地图上留下位置。', text: `${nodes.length} 个教材知识点，连接成这个学生的学习档案。我们不排名，也不把未知当成不会。` },
    { eyebrow: '进入章节', title: `${plan.chapterName}，值得停下来看看。`, text: '镜头靠近当前最有记录、也最需要关注的章节。颜色只表示记录与状态，位置本身没有好坏。' },
    { eyebrow: '一个知识点', title: `${activeNode.name}，不只是一个墨点。`, text: pending ? '新的作答正在整理，完成前先保留下一次判断。' : `${status.label}。${reasonText(activeNode.learning_status, activeNode.reason_codes)}` },
    { eyebrow: '知识之间的联系', title: '理解，从来不是孤立发生的。', text: plan.relationNames.length ? `与它直接相连的是：${plan.relationNames.join('、')}。这些关系来自教材结构，不代表其他节点也存在困难。` : '这个节点暂时没有已确认的直接前置关系。没有关系，不代表它不重要。' },
    { eyebrow: '回到真实作业', title: '每个结论，都要回到那一页作业。', text: '从墨点回到原题、原作答和订正过程。能追溯证据，才是这张地图真正有意义的地方。' },
  ][step]

  return <div ref={root} className={`observatory-tour step-${step}`} role="dialog" aria-modal="true" aria-label="成长讲解展示模式">
    <header className="observatory-header">
      <div className="observatory-brand"><span className="observatory-brand-mark">学</span><div><strong>成长讲解</strong><small>这一学期的学习档案</small></div></div>
      <div className="observatory-progress" aria-label="讲解进度">{STOP_NAMES.map((name, index) => <button key={name} className={index === step ? 'active' : index < step ? 'passed' : ''} onClick={() => go(index)} aria-current={index === step ? 'step' : undefined}><i /><span>{name}</span></button>)}</div>
      <button ref={closeButton} className="observatory-close" onClick={onClose} aria-label="退出展示模式"><X size={19} /><span>退出</span></button>
    </header>

    <div className="observatory-main">
      <section className="observatory-stage" aria-label="星图镜头"><StarMap key={step === 0 ? 'global' : plan.chapterId} nodes={shownNodes} edges={edges} pending={pending} selectedId={activeId} onOpen={explore} /></section>
      {step < 4 ? <aside className="observatory-narrative" key={step}>
        <span className="observatory-step-number">0{step + 1}<small> / 05</small></span>
        <span className="observatory-eyebrow">{copy.eyebrow}</span>
        <h2>{copy.title}</h2><p>{copy.text}</p>
        {step === 2 && <div className={`observatory-state ${activeNode.learning_status}`}><i /><span>{pending ? '正在更新' : status.label}</span><small>{pending ? '等待最新记录' : sourceLabel(activeNode.assessment_source)}</small></div>}
        {step === 3 && plan.relationNames.length > 0 && <div className="observatory-relations">{plan.relationNames.map(name => <span key={name}>{name}</span>)}</div>}
        <div className="observatory-narrative-foot"><BookOpen size={15} /><span>只呈现真实记录，不生成掌握度分数</span></div>
      </aside> : <aside ref={evidencePanel} className="observatory-evidence" aria-label="真实作答证据">
        <div className="observatory-evidence-heading"><span className="observatory-eyebrow">原题与证据</span><h2>{activeNode.name}</h2><p>{copy.text}</p></div>
        {!activeDetail ? <div className="observatory-evidence-loading" role="status"><LoaderCircle className="spin" size={22} /><span>正在调取这个知识点的作答证据…</span></div>
          : <div className="observatory-evidence-content">
            <div className="observatory-evidence-meta"><span className={`observatory-state ${activeDetail.state?.learning_status ?? 'no_judgment'}`}><i />{statusLabels[activeDetail.state?.learning_status ?? 'no_judgment'].label}</span><small>{activeDetail.evidence.length} 条有效记录</small></div>
            {activeDetail.evidence.length ? <ol>{[...activeDetail.evidence].sort((a, b) => b.occurred_at.localeCompare(a.occurred_at)).slice(0, 3).map((item, index) => <li key={item.id}>
              <span className="observatory-evidence-index">0{index + 1}</span><div><div className="observatory-evidence-date"><time dateTime={item.occurred_at}>{dayLabel(item.occurred_at)}</time><span>{eventLabel(item.event_type)}</span><span>{item.source_role === 'model' ? 'AI 初判' : item.source_role === 'sitting_teacher' ? '老师核实' : '来源待核实'}</span></div>
                {item.homework_title && <small>{item.homework_title}</small>}<p>{item.question_text || '这条记录暂未附题目文字。'}</p>
                {item.answer_text && <div className="observatory-evidence-answer"><small>学生作答</small><p>{item.answer_text}</p></div>}
                <div className="observatory-evidence-result"><strong>{resultLabels[item.result]}</strong><span>{assistanceLabel(item.assistance_level)}</span></div>
                {item.homework_id && item.case_id && onOpenHomework && <button disabled={busy} onClick={() => { onClose(); onOpenHomework(item.homework_id!, item.case_id!, evidenceTarget(activeDetail, item)) }}>查看原题与订正 <ArrowUpRight size={15} /></button>}
              </div></li>)}</ol> : <div className="observatory-evidence-loading"><BookOpen size={22} /><span>这里还没有可展示的有效作答，未知不等于不会。</span></div>}
            <p className="observatory-evidence-notice">{activeDetail.notice}</p>
          </div>}
      </aside>}
    </div>

    <footer className="observatory-controls">
      <button onClick={() => go(step - 1)} disabled={step === 0} aria-label="上一个镜头"><ArrowLeft size={17} /><span>上一幕</span></button>
      <button className="observatory-play" onClick={() => {
        if (step === 4) { setStep(0); setPlaying(true) } else setPlaying(current => !current)
      }}>{step === 4 ? <RotateCcw size={17} /> : playing ? <Pause size={17} /> : <Play size={17} />}<span>{step === 4 ? '再讲一遍' : playing ? '暂停讲解' : step === 0 ? '开始讲解' : '继续讲解'}</span></button>
      <span className="observatory-keyboard">空格 暂停 <i /> 方向键 切换 <i /> Esc 退出</span>
      <button onClick={() => go(step + 1)} disabled={step === 4} aria-label="下一个镜头"><span>下一幕</span><ArrowRight size={17} /></button>
    </footer>
  </div>
}
