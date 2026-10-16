import { useState } from 'react'
import { ArrowRight, LoaderCircle, UserRound } from 'lucide-react'
import { LEARNING_STATUSES } from '../api/learningTypes'
import { dateTime, openQuestionLabels, reasonLabels, statusLabels } from '../learning'
import KnowledgeDetailPanel from './KnowledgeDetailPanel'
import { LoadNote, RefreshButton, StatusTag, StudentPicker, useLoad, type LearningContext } from './LearningCommon'
import LearningProfileEditor from './LearningProfileEditor'

export default function StudentProfileArea({ ctx, studentId, onStudent }: { ctx: LearningContext; studentId: string | null; onStudent: (id: string) => void }) {
  const knowledge = useLoad(studentId, signal => ctx.learning.knowledge(studentId!, signal), ctx.version)
  const [chosen, setChosen] = useState<string | null>(null)
  const data = knowledge.data
  const pending = !!data?.projection.pending
  const states = data?.states ?? []
  const active = chosen && states.some(state => state.knowledge_id === chosen) ? chosen : null

  return <main id="main" className="learning-area">
    <aside className="learning-side" aria-label="学员与知识点">
      <div className="queue-heading"><div><h2>学员画像</h2><p>只依据老师已核实、已确认知识点的作答</p></div>{studentId && <RefreshButton state={knowledge} label="刷新学员画像" />}</div>
      <div className="side-block"><StudentPicker students={ctx.students} value={studentId} onChange={id => { onStudent(id); setChosen(null) }} /></div>
      {pending && <p className="pending-note" role="status"><LoaderCircle size={13} className="spin" />正在汇总最新改判，下面的状态可能稍后更新。</p>}
      <div className="queue-list">
        <LoadNote state={knowledge}>
          {states.map(state => <button key={state.knowledge_id} className={`queue-item ${state.knowledge_id === active ? 'selected' : ''}`} onClick={() => setChosen(state.knowledge_id)}>
            <div className="queue-item-top"><StatusTag status={state.learning_status} /><span className="queue-status muted">{state.chapter_name ?? ''}</span></div>
            <h3>{state.name}</h3><p>{state.reason_codes.map(code => reasonLabels[code] ?? code).join('；') || state.sufficiency_explanation}</p>
            <footer><span>{state.open_questions.map(q => openQuestionLabels[q.code] ?? q.code).join(' · ') || '已核实作答 ' + state.scorable_evidence_count + ' 次'}</span><ArrowRight size={15} /></footer>
          </button>)}
          {data && states.length === 0 && <div className="queue-empty"><p>还没有任何知识点状态</p><span>没有记录表示资料不足，不代表薄弱。</span></div>}
        </LoadNote>
      </div>
    </aside>
    <section className="learning-main">
      {!studentId ? <div className="learning-empty"><span className="round-icon"><UserRound size={23} /></span><h2>选择一位学员</h2><p>查看经老师核实的知识点状态、证据与诊断，并维护下一课 / 当前课次。</p></div> : <>
        {data && <div className="card">
          <p className="subtle">{data.notice}</p>
          <p className="subtle">课程图谱 {data.graph_version}{data.graph_review_status.includes('unreviewed') ? '（试点版，尚未经教研审定）' : ''}{data.projection.projected_at ? ` · 汇总于 ${dateTime(data.projection.projected_at)}` : ''}</p>
          {data.chapters.length > 0 && <table className="rollup">
            <caption>按章节汇总（只计数，不做平均）</caption>
            <thead><tr><th scope="col">章节</th>{LEARNING_STATUSES.map(status => <th key={status} scope="col">{statusLabels[status]}</th>)}</tr></thead>
            <tbody>{data.chapters.map(chapter => <tr key={chapter.chapter_id}><th scope="row">{chapter.name}<small> · {chapter.objectives} 个知识点</small></th>
              {LEARNING_STATUSES.map(status => <td key={status}>{chapter.status_counts[status] ?? 0}</td>)}</tr>)}</tbody>
          </table>}
        </div>}
        <LearningProfileEditor key={studentId} ctx={ctx} studentId={studentId} />
        {active ? <KnowledgeDetailPanel key={`${studentId}/${active}`} ctx={ctx} studentId={studentId} knowledgeId={active} pending={pending} />
          : <p className="subtle">从左侧选择一个知识点，查看证据、前置关系和诊断。</p>}
      </>}
    </section>
  </main>
}
