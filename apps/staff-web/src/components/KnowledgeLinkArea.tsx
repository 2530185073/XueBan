import { useState } from 'react'
import { ArrowRight, Inbox, Network } from 'lucide-react'
import type { LinkQueueItem } from '../api/learningTypes'
import { verdictLabels } from '../learning'
import KnowledgeLinkEditor from './KnowledgeLinkEditor'
import { LoadNote, RefreshButton, useLoad, type LearningContext } from './LearningCommon'

export default function KnowledgeLinkArea({ ctx, onOpenCase }: { ctx: LearningContext; onOpenCase: (caseId: string) => void }) {
  const queue = useLoad('link-queue', signal => ctx.learning.linkQueue(signal), ctx.version)
  const [item, setItem] = useState<LinkQueueItem | null>(null)
  const names = new Map(ctx.students.map(student => [student.student_id, student.display_name]))
  const items = queue.data?.items ?? []
  const chosen = item?.case_id ?? null
  const done = !!item && !!queue.data && !items.some(entry => entry.case_id === item.case_id)

  return <main id="main" className="learning-area">
    <aside className="learning-side" aria-label="待确认知识点的题目">
      <div className="queue-heading"><div><h2>知识点映射确认</h2><p>老师已核实对错、但还没有确认知识点的题</p></div><RefreshButton state={queue} label="刷新待确认列表" /></div>
      <div className="queue-list">
        <LoadNote state={queue}>
          {items.map(entry => <button key={entry.case_id} className={`queue-item ${entry.case_id === chosen ? 'selected' : ''}`} onClick={() => setItem(entry)}>
            <div className="queue-item-top"><span className="queue-avatar"><Network size={15} /></span><span className="tag">{verdictLabels[entry.verdict] ?? entry.verdict}</span>
              {entry.candidates.length > 0 && <span className="tag amber">AI 候选 {entry.candidates.length}</span>}</div>
            <h3>{names.get(entry.student_id) ?? '已授权学生'}</h3><p>{entry.question_text ?? '（题干待识别）'}</p>
            <footer><span>{entry.homework_title ?? '未命名作业'}</span><ArrowRight size={15} /></footer>
          </button>)}
          {queue.data && items.length === 0 && <div className="queue-empty"><Inbox size={30} strokeWidth={1.2} /><p>没有待确认的题目</p><span>老师核实对错后，题目会出现在这里等待确认知识点。</span></div>}
        </LoadNote>
      </div>
    </aside>
    <section className="learning-main">
      {item ? <>
        <div className="card">
          <p className="eyebrow">{names.get(item.student_id) ?? '已授权学生'} · {item.homework_title ?? '未命名作业'}</p>
          <h2 className="card-title">{item.question_text ?? '（题干待识别）'}</h2>
          <p className="subtle">老师核实结果：{verdictLabels[item.verdict] ?? item.verdict}</p>
          {done && <p className="preview-note">已有确认的知识点，这道题已移出待确认列表；仍可在下方补充次要知识点。</p>}
          <div className="button-row"><button className="button" onClick={() => onOpenCase(item.case_id)}>在工作台打开这道题<ArrowRight size={14} /></button></div>
        </div>
        <div className="card flush"><KnowledgeLinkEditor key={item.question_id} ctx={ctx} questionId={item.question_id} /></div>
      </> : <div className="learning-empty"><span className="round-icon"><Network size={23} /></span><h2>先确认知识点，作答才会成为证据</h2>
        <p>AI 只提供候选和理由；老师确认主要（定位错误）和次要知识点后，已核实的对错才会进入学员画像。没有确认的题不会让任何知识点显得薄弱。</p></div>}
    </section>
  </main>
}
