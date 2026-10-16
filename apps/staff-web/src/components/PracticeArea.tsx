import { useEffect, useMemo, useState } from 'react'
import { Archive, Check, Clock3, Library, Plus, Sparkles, X } from 'lucide-react'
import type { AnswerKind, Gate, ItemEdits, PracticeItem } from '../api/learningTypes'
import {
  answerKindLabels, dateTime, exitReasonLabels, gateReasonLabels, itemIssue, itemStatusLabels, parseAnswerValues, resultLabels, selectableNodes, sessionStatusLabels,
} from '../learning'
import { chapterNames, LoadNote, NodePicker, RefreshButton, StatusTag, StudentPicker, useGraph, useLoad, type LearningContext } from './LearningCommon'

type Tab = 'bank' | 'window'

interface ItemDraft { stem: string; answers: string; hint: string; solution: string; difficulty: number }
const draftOf = (item: PracticeItem): ItemDraft => ({ stem: item.stem_text, answers: item.answer_values.join('\n'), hint: item.hint_text ?? '', solution: item.solution_text, difficulty: item.difficulty })

function ItemCard({ ctx, item, name }: { ctx: LearningContext; item: PracticeItem; name: string }) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(() => draftOf(item))
  useEffect(() => { setDraft(draftOf(item)); setEditing(false) }, [item.id, item.revision])
  const answers = parseAnswerValues(draft.answers)
  const issue = editing ? itemIssue({ knowledge_id: item.knowledge_id, stem_text: draft.stem, answer_values: answers, solution_text: draft.solution, hint_text: draft.hint || null }) : null

  function edits(): ItemEdits | undefined {
    if (!editing) return undefined
    const change: ItemEdits = {}
    if (draft.stem.trim() !== item.stem_text) change.stem_text = draft.stem.trim()
    if (answers.join('\n') !== item.answer_values.join('\n')) change.answer_values = answers
    if ((draft.hint.trim() || null) !== item.hint_text) change.hint_text = draft.hint.trim() || null
    if (draft.solution.trim() !== item.solution_text) change.solution_text = draft.solution.trim()
    if (draft.difficulty !== item.difficulty) change.difficulty = draft.difficulty
    return Object.keys(change).length ? change : undefined
  }
  const review = (action: 'approve' | 'reject' | 'retire', success: string) =>
    ctx.act(() => ctx.learning.reviewItem(item.id, { action, expected_revision: item.revision, ...(action === 'approve' && edits() ? { edits: edits() } : {}) }), success)

  return <li>
    <div className="help-row"><span className={`tag ${item.status === 'candidate' ? 'amber' : ''}`}>{itemStatusLabels[item.status]}</span><span className="tag muted">{item.source === 'model' ? 'AI 生成' : '老师录入'}</span>
      <span className="tag muted">{name}</span><span className="tag muted">难度 {item.difficulty}</span><time>{dateTime(item.created_at)}</time></div>
    {editing ? <div className="tool-form">
      <label>题干<textarea value={draft.stem} maxLength={600} onChange={event => setDraft({ ...draft, stem: event.target.value })} /></label>
      <div className="form-grid">
        <label>可接受的答案（{answerKindLabels[item.answer_kind]}，每行一个）<textarea value={draft.answers} onChange={event => setDraft({ ...draft, answers: event.target.value })} /></label>
        <label>难度<div className="select-wrap"><select value={draft.difficulty} onChange={event => setDraft({ ...draft, difficulty: Number(event.target.value) })}><option value={1}>1</option><option value={2}>2</option><option value={3}>3</option></select></div></label>
      </div>
      <label>提示（只指方向，不给答案）<input value={draft.hint} maxLength={200} onChange={event => setDraft({ ...draft, hint: event.target.value })} /></label>
      <label>解题步骤<textarea value={draft.solution} maxLength={800} onChange={event => setDraft({ ...draft, solution: event.target.value })} /></label>
      {issue && <small className="form-issue">{issue}</small>}
    </div> : <>
      <p className="answer-line">{item.stem_text}</p>
      <small>答案（{answerKindLabels[item.answer_kind]}）：{item.answer_values.join(' / ')}{item.hint_text && ` · 提示：${item.hint_text}`}</small>
      <small>解题：{item.solution_text}</small>
    </>}
    <div className="button-row wrap">
      {item.status === 'candidate' && <>
        <button className="button primary" disabled={ctx.busy || !!issue} onClick={() => void review('approve', editing ? '已修改并通过，孩子在选做时可能看到这道题。' : '已通过，孩子在选做时可能看到这道题。')}><Check size={14} />{editing ? '保存修改并通过' : '通过'}</button>
        <button className="button" disabled={ctx.busy} onClick={() => setEditing(!editing)}>{editing ? '取消修改' : '修改后通过'}</button>
        <button className="button" disabled={ctx.busy} onClick={() => void review('reject', '已驳回这道候选题。')}><X size={14} />驳回</button>
      </>}
      {item.status === 'approved' && <button className="button" disabled={ctx.busy} onClick={() => void review('retire', '已停用，之后不会再出给孩子。')}><Archive size={14} />停用</button>}
    </div>
  </li>
}

function Bank({ ctx }: { ctx: LearningContext }) {
  const graph = useGraph(ctx.learning)
  const nodes = useMemo(() => selectableNodes(graph.data?.nodes ?? []), [graph.data])
  const chapters = useMemo(() => chapterNames(graph.data?.nodes ?? []), [graph.data])
  const names = useMemo(() => new Map(nodes.map(node => [node.id, node.name])), [nodes])
  const [status, setStatus] = useState<'candidate' | 'approved'>('candidate')
  const [filter, setFilter] = useState<string | null>(null)
  const items = useLoad(`${status}:${filter ?? ''}`, signal => ctx.learning.practiceItems(status, filter, signal), ctx.version)
  const [knowledge, setKnowledge] = useState<string | null>(null)
  const [stem, setStem] = useState('')
  const [kind, setKind] = useState<AnswerKind>('number')
  const [answers, setAnswers] = useState('')
  const [hint, setHint] = useState('')
  const [solution, setSolution] = useState('')
  const [difficulty, setDifficulty] = useState(1)
  const [count, setCount] = useState(3)
  const [sourceCase, setSourceCase] = useState('')
  const values = parseAnswerValues(answers)
  const issue = itemIssue({ knowledge_id: knowledge ?? '', stem_text: stem, answer_values: values, solution_text: solution, hint_text: hint || null })

  async function create() {
    if (issue || !knowledge) return
    const made = await ctx.act(() => ctx.learning.createItem({ knowledge_id: knowledge, stem_text: stem.trim(), answer_kind: kind, answer_values: values, hint_text: hint.trim() || null, solution_text: solution.trim(), difficulty }),
      '题目已录入并直接生效（老师录入无需再审核）。')
    if (made) { setStem(''); setAnswers(''); setHint(''); setSolution('') }
  }
  async function generate() {
    if (!knowledge) return
    const source = sourceCase.trim()
    await ctx.act(() => ctx.learning.generateItems({ knowledge_id: knowledge, count, ...(source ? { source_case_id: source } : {}) }), '已请求 AI 生成候选题；生成后出现在“待审核”中，老师通过后孩子才会看到。')
  }

  return <>
    <div className="card">
      <div className="section-heading"><span><Library size={17} />题库审核</span><RefreshButton state={items} label="刷新题库" /></div>
      <p className="subtle">AI 只生成候选题；只有老师通过的题才会出现在孩子自愿的选做里。选做结果只按规则判分，不计入知识点判断。</p>
      <div className="form-grid">
        <div className="queue-switch" role="tablist" aria-label="题目状态">{(['candidate', 'approved'] as const).map(value =>
          <button key={value} role="tab" aria-selected={status === value} className={status === value ? 'active' : ''} onClick={() => setStatus(value)}>{itemStatusLabels[value]}</button>)}</div>
        <label>按知识点筛选<div className="select-wrap"><select value={filter ?? ''} onChange={event => setFilter(event.target.value || null)}>
          <option value="">全部知识点</option>{nodes.map(node => <option key={node.id} value={node.id}>{node.name}</option>)}</select></div></label>
      </div>
      <LoadNote state={items}>
        {items.data && items.data.items.length === 0 && <p className="subtle">{status === 'candidate' ? '没有待审核的题目。' : '还没有已通过的题目。'}</p>}
        <ul className="hint-list item-list">{(items.data?.items ?? []).map(item => <ItemCard key={item.id} ctx={ctx} item={item} name={names.get(item.knowledge_id) ?? item.knowledge_id} />)}</ul>
      </LoadNote>
    </div>
    <div className="card">
      <div className="section-heading"><span><Plus size={17} />录入或生成题目</span></div>
      <div className="tool-form">
        <LoadNote state={graph}><NodePicker nodes={nodes} chapters={chapters} selected={knowledge ? [knowledge] : []} max={1} disabled={ctx.busy} label="知识点（只能选具体知识点）" onToggle={id => setKnowledge(old => old === id ? null : id)} /></LoadNote>
        <label>题干<textarea value={stem} maxLength={600} disabled={ctx.busy} placeholder="例如：计算：(-3) + 5 = ?" onChange={event => setStem(event.target.value)} /></label>
        <div className="form-grid">
          <label>答案类型<div className="select-wrap"><select value={kind} disabled={ctx.busy} onChange={event => setKind(event.target.value as AnswerKind)}>
            <option value="number">数值（分数写 a/b）</option><option value="choice">选择题（填选项字母）</option><option value="text">文字（最简答案）</option></select></div></label>
          <label>难度<div className="select-wrap"><select value={difficulty} disabled={ctx.busy} onChange={event => setDifficulty(Number(event.target.value))}><option value={1}>1</option><option value={2}>2</option><option value={3}>3</option></select></div></label>
        </div>
        <label>可接受的答案（1–4 个，逗号或换行分隔）<textarea value={answers} disabled={ctx.busy} onChange={event => setAnswers(event.target.value)} /></label>
        <label>提示（可选，只指方向不给答案）<input value={hint} maxLength={200} disabled={ctx.busy} onChange={event => setHint(event.target.value)} /></label>
        <label>解题步骤<textarea value={solution} maxLength={800} disabled={ctx.busy} onChange={event => setSolution(event.target.value)} /></label>
        {(stem || answers || solution) && issue && <small className="form-issue">{issue}</small>}
        <button className="button primary" disabled={ctx.busy || !!issue} onClick={() => void create()}><Check size={14} />录入题目（直接生效）</button>
        <hr />
        <div className="form-grid">
          <label>AI 生成数量<div className="select-wrap"><select value={count} disabled={ctx.busy} onChange={event => setCount(Number(event.target.value))}>{[1, 2, 3, 4, 5].map(n => <option key={n} value={n}>{n}</option>)}</select></div></label>
          <label>参考题目 case 编号（可选）<input value={sourceCase} maxLength={64} disabled={ctx.busy} placeholder="case_…" onChange={event => setSourceCase(event.target.value)} /></label>
        </div>
        <button className="button" disabled={ctx.busy || !knowledge} onClick={() => void generate()}><Sparkles size={14} />为所选知识点生成候选题</button>
      </div>
    </div>
  </>
}

const tasksChoice = (gate: Gate) => gate.school_tasks_complete.source === 'staff' && gate.school_tasks_complete.value !== null ? String(gate.school_tasks_complete.value) : ''

function Window({ ctx, studentId }: { ctx: LearningContext; studentId: string }) {
  const activities = useLoad(studentId, signal => ctx.learning.activities(studentId, signal), ctx.version)
  const sessions = useLoad(studentId, signal => ctx.learning.sessions(studentId, signal), ctx.version)
  const gate = activities.data?.gate ?? null
  const [capacity, setCapacity] = useState(false)
  const [minutes, setMinutes] = useState(0)
  const [tasks, setTasks] = useState('')
  useEffect(() => {
    if (!gate) return
    setCapacity(gate.has_capacity.value === true); setMinutes(gate.extra_minutes); setTasks(tasksChoice(gate))
  }, [studentId, gate?.window_revision])
  const minutesOk = Number.isInteger(minutes) && minutes >= 0 && minutes <= 60

  async function save() {
    if (!gate || !minutesOk) return
    await ctx.act(() => ctx.learning.setWindow(studentId, { expected_revision: gate.window_revision, capacity_observed: capacity, extra_minutes: minutes, school_tasks_confirmed: tasks === '' ? null : tasks === 'true' }),
      '今天的选做窗口已保存。')
  }
  const system = gate?.school_tasks_complete

  return <>
    <div className="card">
      <div className="section-heading"><span><Clock3 size={17} />今日选做窗口</span><RefreshButton state={activities} label="刷新选做窗口" /></div>
      <p className="notice-strip">额外活动默认 0 分钟，必须孩子愿意、学校任务完成、老师确认有余力；不做不记欠账。</p>
      <LoadNote state={activities}>{gate && activities.data && <>
        <div className={`gate ${gate.open ? 'open' : 'closed'}`} role="status">
          <strong>{gate.open ? `已开放：孩子可以自愿选择，还剩约 ${gate.remaining_minutes} 分钟` : '未开放：孩子现在看不到额外活动'}</strong>
          {gate.reasons.length > 0 && <ul className="plain-list">{gate.reasons.map(reason => <li key={reason}>{gateReasonLabels[reason]}</li>)}</ul>}
          <small>学校任务：{system?.value === true ? '已完成' : system?.value === false ? '未完成' : '未知'}（{system?.source === 'staff' ? '老师确认' : '系统按今日拍照作业核对'}
            {system?.source === 'system' && system.detail === 'no_homework_today' ? '，今天没有拍照作业' : ''}）· 今日额外时间 {gate.extra_minutes} 分钟 · 核对于 {dateTime(gate.checked_at)}</small>
        </div>
        <div className="tool-form">
          <label className="check"><input type="checkbox" checked={capacity} disabled={ctx.busy} onChange={event => setCapacity(event.target.checked)} />老师已观察到孩子今天还有余力（精神状态、时间都允许）</label>
          <div className="form-grid">
            <label>额外时间（0–60 分钟）<input type="number" min={0} max={60} step={5} value={minutes} disabled={ctx.busy} onChange={event => setMinutes(Number(event.target.value))} /></label>
            <label>学校任务<div className="select-wrap"><select value={tasks} disabled={ctx.busy} onChange={event => setTasks(event.target.value)}>
              <option value="">按系统核对（不由老师覆盖）</option><option value="true">老师确认今天学校任务已完成</option><option value="false">老师确认还有学校任务没完成</option></select></div></label>
          </div>
          {!minutesOk && <small className="form-issue">额外时间须为 0–60 的整数分钟。</small>}
          <small className="subtle">开放后由孩子自己决定是否开始，随时可以结束；中途结束或不做都不记欠账，也不影响知识地图。</small>
          <button className="button primary" disabled={ctx.busy || !minutesOk} onClick={() => void save()}><Check size={14} />保存今天的设置</button>
        </div>
        <div className="related">
          <p className="field-title">孩子在开放后可以选择</p>
          {activities.data.variants.length === 0 && !activities.data.preview && <p className="subtle">目前没有可选的活动（需要“当前需要帮助 / 正在巩固”的知识点有审核通过的题，或已填写当前课次）。</p>}
          {activities.data.variants.map(variant => <p key={variant.knowledge_id}>同类题练习：{variant.name} <StatusTag status={variant.learning_status} /> · 可用 {variant.available_items} 题</p>)}
          {activities.data.preview && <p>下一课预习：{activities.data.preview.names.join('、')}（小测可用 {activities.data.preview.quiz_items_available} 题）</p>}
        </div>
      </>}</LoadNote>
    </div>
    <div className="card">
      <div className="section-heading"><span><Library size={17} />选做记录（只读）</span><RefreshButton state={sessions} label="刷新选做记录" /></div>
      <LoadNote state={sessions}>
        {sessions.data && sessions.data.items.length === 0 && <p className="subtle">还没有选做记录。没有记录不代表欠了什么。</p>}
        <ul className="hint-list">{(sessions.data?.items ?? []).map(session => <li key={session.id}>
          <div className="help-row"><span className="tag">{session.purpose === 'variant' ? '同类题练习' : '下一课预习'}</span><span className="tag muted">{sessionStatusLabels[session.status] ?? session.status}</span>
            {session.exit_reason && <span className="tag muted">{exitReasonLabels[session.exit_reason] ?? session.exit_reason}</span>}<time>{dateTime(session.started_at)}</time></div>
          {session.items.length === 0 ? <small>只阅读预习内容，没有小测。</small> : <ul className="plain-list">{session.items.map(entry => <li key={entry.id}>{entry.stem_text}
            {entry.response ? ` —— ${resultLabels[entry.response.result] ?? entry.response.result}${entry.response.hint_used ? '（看过提示）' : ''}` : ' —— 未作答'}</li>)}</ul>}
        </li>)}</ul>
      </LoadNote>
    </div>
  </>
}

export default function PracticeArea({ ctx, studentId, onStudent }: { ctx: LearningContext; studentId: string | null; onStudent: (id: string) => void }) {
  const [tab, setTab] = useState<Tab>('bank')
  return <main id="main" className="learning-area">
    <aside className="learning-side" aria-label="题库与选做">
      <div className="queue-heading"><div><h2>题库审核与选做</h2><p>额外活动完全自愿，默认不加题</p></div></div>
      <div className="queue-switch" role="tablist" aria-label="题库与选做">
        <button role="tab" aria-selected={tab === 'bank'} className={tab === 'bank' ? 'active' : ''} onClick={() => setTab('bank')}><Library size={15} />题库审核</button>
        <button role="tab" aria-selected={tab === 'window'} className={tab === 'window' ? 'active' : ''} onClick={() => setTab('window')}><Clock3 size={15} />今日选做</button>
      </div>
      {tab === 'window' && <div className="side-block"><StudentPicker students={ctx.students} value={studentId} onChange={onStudent} /></div>}
      <div className="principle"><span>01</span><p>学校任务优先；完成后可以休息或结束</p></div>
      <div className="principle"><span>02</span><p>老师确认有余力并开放时间，孩子自己决定做不做</p></div>
      <div className="principle"><span>03</span><p>选做结果不计入知识点判断，不做不记欠账</p></div>
    </aside>
    <section className="learning-main">
      {tab === 'bank' ? <Bank ctx={ctx} /> : studentId ? <Window key={studentId} ctx={ctx} studentId={studentId} /> : <p className="subtle">请先在左侧选择学员。</p>}
    </section>
  </main>
}
