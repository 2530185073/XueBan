import { useEffect, useRef, useState } from 'react'
import { ArrowLeft, BookOpen, ChevronRight, List, Map as MapIcon, X } from 'lucide-react'
import { StarMap } from '../components/StarMap'
import { MathText } from '../components/MathText'
import { statusLabels } from '../learning'
import { DEMO_EDGES, DEMO_NODES, DEMO_SUBJECTS, type DemoTopic } from './knowledgeDemoData'
import './knowledge-demo.css'

function DemoDetail({ topic, onClose }: { topic: DemoTopic; onClose: () => void }) {
  const heading = useRef<HTMLHeadingElement>(null)
  useEffect(() => { heading.current?.focus() }, [topic.id])
  const status = statusLabels[topic.status]
  return <section className="knowledge-detail" aria-label="模拟知识点详情">
    <div className="knowledge-detail-top"><span className="eyebrow">模拟数据 · 非真实学习评价</span><button className="icon-button" onClick={onClose} aria-label="关闭模拟知识点详情"><X size={19} /></button></div>
    <h2 ref={heading} tabIndex={-1}>{topic.name}</h2>
    <div className="knowledge-detail-status"><span className={`status-pill ${status.tone}`}>{status.label}</span><span className="knowledge-source-chip">演示状态</span></div>
    <p className="knowledge-reason">{topic.status === 'needs_help_now' ? '模拟情境：这类题最近做错，需要再练一练。' : topic.status === 'consolidating' ? '模拟情境：已经订正，之后再用一道新题检验。' : topic.status === 'stable_for_now' ? '模拟情境：这部分已经比较熟练，继续保持。' : '模拟情境：暂时没有作答记录，不代表不会。'}</p>
    <div className="knowledge-objective"><BookOpen size={17} /><div><h3>学会这一点，可以…</h3><p>{topic.objective}</p></div></div>
    {topic.status !== 'no_judgment' ? <div className="knowledge-block"><h3>一条模拟作答</h3><p className="small muted">下面的题目、作答和学习状态均为演示编写。</p>
      <div className="demo-answer"><MathText text={topic.question} /><div><strong>模拟作答：</strong><MathText text={topic.answer} /></div><div><strong>正确答案：</strong><MathText text={topic.correctAnswer} /></div></div>
      <h3>正确的作答步骤</h3><ol className="demo-steps">{topic.steps.map((step, index) => <li key={index}><MathText text={step} /></li>)}</ol>
    </div> : <div className="knowledge-detail-empty"><p>暂无模拟作答</p><small>可以点其他亮起的知识点，查看对错与订正示例。</small></div>}
    <p className="muted small knowledge-notice">仅用来预览产品效果，不写入作业、举一反三或学生掌握度。</p>
  </section>
}

export default function KnowledgeDemoPage({ onExit }: { onExit: () => void }) {
  const [subjectId, setSubjectId] = useState('all')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [view, setView] = useState<'map' | 'list'>('map')
  const subject = DEMO_SUBJECTS.find(item => item.id === subjectId)
  const nodes = DEMO_NODES.filter(node => !subject || node.chapter_id === subject.id)
  const selected = DEMO_SUBJECTS.flatMap(item => item.topics).find(topic => topic.id === selectedId)
  return <section className="knowledge-page knowledge-demo">
    <div className="page-title-row"><div><span className="eyebrow">初一 · 虚拟学生</span><h1>我的知识地图</h1><p>点开知识点，看看哪里熟练、哪里需要再练。</p></div><button className="button secondary" onClick={onExit}><ArrowLeft size={16} />退出演示</button></div>
    <div className="demo-disclaimer" role="note"><strong>模拟演示</strong><span>7 门学科 · 28 个知识点。状态、作答与连线均为示例，不代表该学生的真实水平，也不是经核验的教材完整目录。</span></div>
    <nav className="knowledge-subject-tabs" aria-label="模拟学科筛选"><button aria-pressed={!subject} onClick={() => { setSubjectId('all'); setSelectedId(null) }}>全部学科</button>{DEMO_SUBJECTS.map(item => <button key={item.id} aria-pressed={subjectId === item.id} onClick={() => { setSubjectId(item.id); setSelectedId(null) }}>{item.name}</button>)}</nav>
    <div className="demo-overview" aria-label="模拟状态统计">{(['stable_for_now', 'consolidating', 'needs_help_now', 'no_judgment'] as const).map(status => <div key={status}><span className={`knowledge-list-dot ${status}`} /><strong>{nodes.filter(node => node.learning_status === status).length}</strong><span>{statusLabels[status].label}</span></div>)}</div>
    <div className={`knowledge-layout ${selected ? 'has-detail' : ''}`}><section className={`knowledge-map-sheet ${view === 'map' ? 'star' : ''}`} aria-label="模拟知识图谱">
      <div className="knowledge-sheet-toolbar"><div><h3>{subject?.name ?? '七门学科，一眼看见'}</h3><span>{subject ? `${subject.publisher} · 初一知识点示例` : '对应指定的出版社范围 · 每科 4 个示例知识点'}</span></div><div className="knowledge-view-switch" aria-label="演示展示方式"><button aria-label="模拟地图视图" aria-pressed={view === 'map'} onClick={() => setView('map')}><MapIcon size={17} /></button><button aria-label="模拟列表视图" aria-pressed={view === 'list'} onClick={() => setView('list')}><List size={18} /></button></div></div>
      {view === 'map' ? <StarMap nodes={nodes} edges={DEMO_EDGES} pending={false} selectedId={selectedId} onOpen={setSelectedId} demo /> : <div className="knowledge-node-list">{nodes.map(node => <button key={node.id} className={selectedId === node.id ? 'selected' : ''} onClick={() => setSelectedId(node.id)}><span className={`knowledge-list-dot ${node.learning_status}`} /><span><strong>{node.name}</strong><small>{node.subject} · 模拟状态</small></span><span className={`status-pill ${statusLabels[node.learning_status].tone}`}>{statusLabels[node.learning_status].label}</span><ChevronRight size={16} /></button>)}</div>}
      <div className="star-legend" aria-label="模拟状态图例"><span><i className="stable_for_now" />阶段性较稳</span><span><i className="consolidating" />正在巩固</span><span><i className="needs_help_now" />需要再看看</span><span><i className="none" />暂无记录</span><small>仅模拟，不计入真实学习记录</small></div>
    </section>{selected && <aside className="knowledge-detail-panel"><DemoDetail topic={selected} onClose={() => setSelectedId(null)} /></aside>}</div>
    <p className="muted small knowledge-notice">本页仅在本机开发环境、当前指定账号下显示。退出演示即可回到真实图谱；没有调用模型，也没有新增或修改后台学习记录。</p>
  </section>
}
