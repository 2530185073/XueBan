import { useEffect, useMemo, useRef, useState } from 'react'
import { Brain, Check, LoaderCircle, Sparkles, Undo2 } from 'lucide-react'
import type { LearningApi } from '../api/learning'
import { runEvidence, type AgentRun, type Evidence, type Memory, type StaffMemoryCategory } from '../api/learningTypes'
import {
  certaintyLabels, dateTime, failureLabels, MEMORY_MAX, memoryCategoryLabels, memoryIssue, memoryReasonLabels, memorySourceLabels, memoryStatusLabels,
  resultLabels, runView, runViewLabels, shortDate, staffMemoryCategories,
} from '../learning'
import { LoadNote, RefreshButton, StudentPicker, useLoad, type LearningContext } from './LearningCommon'

export interface IndexedEvidence extends Evidence { knowledge_id: string; knowledge_name: string }
const POLL_MS = 2500
const MAX_POLLS = 72

/** Every evidence row of the student's known knowledge points, so cited ids can be shown as facts. */
export async function loadEvidenceIndex(learning: LearningApi, studentId: string, signal: AbortSignal): Promise<Map<string, IndexedEvidence>> {
  const knowledge = await learning.knowledge(studentId, signal)
  const index = new Map<string, IndexedEvidence>()
  const states = knowledge.states.slice(0, 40)
  for (let i = 0; i < states.length; i += 4) {
    const details = await Promise.all(states.slice(i, i + 4).map(state => learning.knowledgeDetail(studentId, state.knowledge_id, signal)))
    for (const detail of details) for (const item of detail.evidence) index.set(item.id, { ...item, knowledge_id: detail.node.id, knowledge_name: detail.node.name })
  }
  return index
}

export function EvidenceChips({ ids, index }: { ids: string[]; index: Map<string, IndexedEvidence> | null }) {
  if (ids.length === 0) return <small className="subtle">未引用证据</small>
  return <span className="chip-row">{ids.map(id => {
    const item = index?.get(id)
    if (!item) return <span key={id} className="chip muted" title={id}>证据 {id.slice(-6)}{index ? ' · 未找到（可能已撤销）' : ''}</span>
    return <span key={id} className={`chip ${item.validity === 'valid' ? '' : 'muted'}`} title={item.question_text ?? undefined}>
      {shortDate(item.occurred_at)} · {item.knowledge_name} · {resultLabels[item.result] ?? item.result}{item.validity === 'valid' ? '' : ' · 已失效'}</span>
  })}</span>
}

function MemoryCard({ memory, index, ctx, names }: { memory: Memory; index: Map<string, IndexedEvidence> | null; ctx: LearningContext; names: Map<string, string> }) {
  const live = memory.status === 'proposed' || memory.status === 'confirmed'
  return <li className={memory.status === 'proposed' ? 'proposed' : ''}>
    <div className="help-row"><span className="tag">{memoryCategoryLabels[memory.category] ?? memory.category}</span>
      <span className={`tag ${memory.status === 'proposed' ? 'amber' : live ? '' : 'muted'}`}>{memoryStatusLabels[memory.status]}</span>
      <span className="tag muted">{memorySourceLabels[memory.source]}</span></div>
    <p>{memory.content}</p>
    <small>{memory.knowledge_id ? `知识点：${names.get(memory.knowledge_id) ?? memory.knowledge_id} · ` : ''}记录于 {dateTime(memory.created_at)}
      {memory.valid_until && ` · 有效至 ${dateTime(memory.valid_until)}`}{memory.status_reason && ` · ${memoryReasonLabels[memory.status_reason] ?? memory.status_reason}`}</small>
    <EvidenceChips ids={memory.evidence_ids} index={index} />
    {live && <div className="button-row">
      {memory.status === 'proposed' && <button className="button primary" disabled={ctx.busy} onClick={() => void ctx.act(() => ctx.learning.confirmMemory(memory.id), '已确认这条记忆。')}><Check size={14} />确认</button>}
      <button className="button" disabled={ctx.busy} onClick={() => void ctx.act(() => ctx.learning.retractMemory(memory.id), memory.status === 'proposed' ? '已拒绝这条提议。' : '已撤回这条记忆。')}><Undo2 size={14} />{memory.status === 'proposed' ? '不采纳' : '撤回'}</button>
    </div>}
  </li>
}

function RunCard({ run, index }: { run: AgentRun; index: Map<string, IndexedEvidence> | null }) {
  const view = runView(run)
  const output = run.output
  return <div className={`run-card run-${view}`}>
    <div className="help-row"><span className={`tag ${view === 'ready' ? 'amber' : view === 'queued' ? '' : 'rose'}`}>{view === 'queued' && <LoaderCircle size={11} className="spin" />}{runViewLabels[view]}</span>
      <time>{dateTime(run.created_at)}</time></div>
    {view === 'failed' && <p className="warning-text">{failureLabels[run.failure_code ?? ''] ?? '生成失败'}，可以稍后重试；这不影响任何学习记录。</p>}
    {view === 'stale' && <p className="warning-text">摘要引用的证据后来被撤销，内容不再可信，仅保留作记录。</p>}
    {view === 'needs_human' && <p className="warning-text">AI 认为现有资料需要老师现场判断，请不要直接采用下面的内容。</p>}
    {output && view !== 'queued' && <>
      <p className="run-summary">{output.summary}</p>
      {output.claims.length > 0 && <ul className="plain-list claims">{output.claims.map((claim, i) => <li key={i}>
        <span>{claim.text}</span> <span className={`tag ${claim.certainty === 'high' ? '' : 'muted'}`}>{certaintyLabels[claim.certainty]}</span>
        <div><EvidenceChips ids={claim.evidence_ids} index={index} /></div></li>)}</ul>}
      {output.next_action && <p><span className="field-title">最小下一步：</span>{output.next_action}</p>}
      <small className="subtle">引用了 {runEvidence(run).length} 条证据。AI 提议的记忆会出现在“待确认”中，由老师决定是否采纳。</small>
    </>}
  </div>
}

export default function MemoryArea({ ctx, studentId, onStudent }: { ctx: LearningContext; studentId: string | null; onStudent: (id: string) => void }) {
  const [poll, setPoll] = useState(0)
  const polls = useRef(0)
  const memories = useLoad(studentId, signal => ctx.learning.memories(studentId!, signal), ctx.version)
  const runs = useLoad(studentId, signal => ctx.learning.runs(studentId!, signal), `${ctx.version}:${poll}`)
  const evidence = useLoad(studentId, signal => loadEvidenceIndex(ctx.learning, studentId!, signal), ctx.version)
  const [category, setCategory] = useState<StaffMemoryCategory>('effective_support')
  const [content, setContent] = useState('')
  const [knowledgeId, setKnowledgeId] = useState('')
  const [picked, setPicked] = useState<string[]>([])
  const index = evidence.data
  const queued = (runs.data?.items ?? []).some(run => run.status === 'queued')

  useEffect(() => { polls.current = 0 }, [studentId, ctx.version])
  useEffect(() => {
    if (!queued || runs.loading || polls.current >= MAX_POLLS) return
    const timer = setTimeout(() => { polls.current += 1; setPoll(n => n + 1) }, POLL_MS)
    return () => clearTimeout(timer)
  }, [queued, runs.loading, runs.data])
  // A finished run may have proposed memories; read them once it is done.
  const wasQueued = useRef(false)
  useEffect(() => { if (wasQueued.current && !queued) memories.reload(); wasQueued.current = queued }, [queued])

  const valid = useMemo(() => [...(index?.values() ?? [])].filter(item => item.validity === 'valid').sort((a, b) => Date.parse(b.occurred_at) - Date.parse(a.occurred_at)), [index])
  const knowledgeNames = useMemo(() => new Map(valid.map(item => [item.knowledge_id, item.knowledge_name])), [valid])
  const choices = knowledgeId ? valid.filter(item => item.knowledge_id === knowledgeId) : valid
  const chosen = picked.filter(id => choices.some(item => item.id === id))
  const input = { category, content, knowledge_id: knowledgeId || null, evidence_ids: chosen }
  const issue = memoryIssue(input, new Set(valid.map(item => item.id)))
  const items = memories.data?.items ?? []
  const proposed = items.filter(m => m.status === 'proposed')
  const confirmed = items.filter(m => m.status === 'confirmed')
  const history = items.filter(m => m.status !== 'proposed' && m.status !== 'confirmed')

  async function create() {
    if (!studentId || issue) return
    const made = await ctx.act(() => ctx.learning.createMemory(studentId, { ...input, content: content.trim() }), '已记下这条记忆。')
    if (made) { setContent(''); setPicked([]) }
  }
  async function summarize() {
    if (!studentId) return
    if (await ctx.act(() => ctx.learning.requestSummary(studentId), '已请求生成 AI 学情摘要，完成后自动显示。')) polls.current = 0
  }

  return <main id="main" className="learning-area">
    <aside className="learning-side" aria-label="学员">
      <div className="queue-heading"><div><h2>学伴记忆</h2><p>每条记忆都有出处，老师确认后才生效</p></div>{studentId && <RefreshButton state={memories} label="刷新学伴记忆" />}</div>
      <div className="side-block"><StudentPicker students={ctx.students} value={studentId} onChange={id => { onStudent(id); setPicked([]); setKnowledgeId('') }} /></div>
      <div className="side-block subtle">{memories.data?.notice ?? '学伴只记住有出处的事实和老师确认的约定；证据被撤销后相关记忆自动失效。'}</div>
      <div className="principle"><span>01</span><p>系统规则只总结已核实的事实，不推测性格或态度</p></div>
      <div className="principle"><span>02</span><p>AI 只能提议，老师确认后才会用于提醒孩子</p></div>
      <div className="principle"><span>03</span><p>学科困难必须引用已核实的作答</p></div>
    </aside>
    {!studentId ? <section className="learning-main"><div className="learning-empty"><span className="round-icon"><Brain size={23} /></span><h2>选择一位学员</h2><p>查看和确认学伴记住的事实、约定与目标，并请求 AI 学情摘要。</p></div></section> :
    <section className="learning-main">
      <div className="card">
        <div className="section-heading"><span><Sparkles size={17} />AI 学情摘要</span><span className="button-row"><RefreshButton state={runs} label="刷新摘要" />
          <button className="button primary" disabled={ctx.busy || queued} onClick={() => void summarize()}><Sparkles size={14} />{queued ? '正在生成…' : '生成新的摘要'}</button></span></div>
        <p className="subtle">AI 只读取已核实的知识状态、证据和已确认记忆；每条判断都列出引用的证据。摘要仅供老师参考，不发给孩子或家长。</p>
        <LoadNote state={runs}>
          {runs.data && runs.data.items.length === 0 && <p className="subtle">还没有生成过摘要。</p>}
          {runs.data?.items[0] && <RunCard run={runs.data.items[0]} index={index} />}
          {queued && polls.current >= MAX_POLLS && <p className="warning-text">仍在排队，请稍后点刷新查看。</p>}
          {(runs.data?.items.length ?? 0) > 1 && <details className="history"><summary>更早的摘要 {runs.data!.items.length - 1} 次</summary>
            {runs.data!.items.slice(1).map(run => <RunCard key={run.id} run={run} index={index} />)}</details>}
        </LoadNote>
      </div>

      <div className="card">
        <div className="section-heading"><span><Brain size={17} />记忆</span><small>待确认 {proposed.length} · 有效 {confirmed.length}</small></div>
        <LoadNote state={memories}>
          {proposed.length > 0 && <><p className="field-title">待老师确认</p><ul className="hint-list memory-list">{proposed.map(m => <MemoryCard key={m.id} memory={m} index={index} ctx={ctx} names={knowledgeNames} />)}</ul></>}
          {confirmed.length > 0 ? <><p className="field-title">有效</p><ul className="hint-list memory-list">{confirmed.map(m => <MemoryCard key={m.id} memory={m} index={index} ctx={ctx} names={knowledgeNames} />)}</ul></>
            : <p className="subtle">还没有有效的记忆。</p>}
          {history.length > 0 && <details className="history"><summary>已失效 / 已撤回 / 已过期 {history.length} 条</summary>
            <ul className="hint-list memory-list">{history.map(m => <MemoryCard key={m.id} memory={m} index={index} ctx={ctx} names={knowledgeNames} />)}</ul></details>}
        </LoadNote>
      </div>

      <div className="card">
        <div className="section-heading"><span><Check size={17} />老师记下一条</span></div>
        <div className="tool-form">
          <div className="form-grid">
            <label>类别<div className="select-wrap"><select value={category} disabled={ctx.busy} onChange={event => setCategory(event.target.value as StaffMemoryCategory)}>
              {staffMemoryCategories.map(value => <option key={value} value={value}>{memoryCategoryLabels[value]}</option>)}</select></div></label>
            <label>相关知识点（可选）<div className="select-wrap"><select value={knowledgeId} disabled={ctx.busy} onChange={event => setKnowledgeId(event.target.value)}>
              <option value="">不限知识点</option>{[...knowledgeNames].map(([id, name]) => <option key={id} value={id}>{name}</option>)}</select></div></label>
          </div>
          <label>内容（{content.length}/{MEMORY_MAX}）<textarea value={content} maxLength={MEMORY_MAX} disabled={ctx.busy}
            placeholder={category === 'support_agreement' ? '例如：先自己读题两遍再求助' : category === 'effective_support' ? '例如：画数轴讲解更容易理解' : '只写事实或约定，不写性格评价'} onChange={event => setContent(event.target.value)} /></label>
          <fieldset className="choice-list evidence-pick" disabled={ctx.busy}><legend>引用证据{category === 'recurring_difficulty' ? '（必选，至少一条）' : '（可选）'}</legend>
            <LoadNote state={evidence}>
              {choices.length === 0 && <p className="subtle">没有当前有效的证据可引用。</p>}
              {choices.map(item => <label key={item.id} className="check"><input type="checkbox" checked={chosen.includes(item.id)} onChange={() => setPicked(old => old.includes(item.id) ? old.filter(x => x !== item.id) : [...old, item.id])} />
                <span>{shortDate(item.occurred_at)} · {item.knowledge_name} · {resultLabels[item.result] ?? item.result} · {item.question_text ?? '（题干待识别）'}</span></label>)}
            </LoadNote>
          </fieldset>
          {(content || chosen.length > 0) && issue && <small className="form-issue">{issue}</small>}
          <button className="button primary" disabled={ctx.busy || !!issue} onClick={() => void create()}><Check size={14} />记下</button>
        </div>
      </div>
    </section>}
  </main>
}
