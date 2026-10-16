import { useEffect, useMemo, useState } from 'react'
import { CheckCircle2, Crop, Eye, LoaderCircle, MessageCircle, PencilLine, Plus, Scissors, Trash2, TriangleAlert, X } from 'lucide-react'
import type { HelpRequest, ImageAsset, Job, QuestionRead, Region, RepairLayout, ReviseAnswer, ReviseQuestion } from '../api/contracts.generated'
import { jobLabels, isTerminal } from '../jobs'
import { kindLabels } from '../queue'
import { attemptChain, buildRepair, currentAttempt, MAX_REPLACEMENTS, repairIssue, type CaseWorkspace, type ReplacementDraft } from '../workspace'
import PhotoViewer, { type DrawingTool, type PhotoLayers } from './PhotoViewer'

export type DeskMode = 'view' | 'question' | 'answer' | 'layout'
export interface RepairState { job: Job; homeworkId: string; createdCaseIds: string[] | null }

const modes: [DeskMode, string, typeof Eye][] = [['view', '查看', Eye], ['question', '修订题目', PencilLine], ['answer', '修订答案', Crop], ['layout', '补题 / 拆合', Scissors]]

function RegionList({ regions, onRemove, fallback }: { regions: Region[]; onRemove: (index: number) => void; fallback: string }) {
  if (regions.length === 0) return <p className="subtle">{fallback}</p>
  return <ul className="region-list">{regions.map((region, i) => <li key={i}><span>第 {i + 1} 框 · {region.width}×{region.height} @ ({region.x}, {region.y})</span>
    <button className="icon-button small" aria-label={`删除第 ${i + 1} 框`} onClick={() => onRemove(i)}><X size={14} /></button></li>)}</ul>
}

export default function CaseDesk({ ws, studentName, focus, block, busy, sample, attemptId, loadImage, loadQuestions, onReviseQuestion, onReviseAnswer, onRepair, repair, onOpenCase }: {
  ws: CaseWorkspace; studentName: string; focus: HelpRequest | null; block: string | null; busy: boolean; sample: boolean
  attemptId: string; loadImage: (asset: ImageAsset, signal: AbortSignal) => Promise<string>; loadQuestions: () => Promise<QuestionRead[]>
  onReviseQuestion: (questionId: string, payload: ReviseQuestion) => Promise<boolean>
  onReviseAnswer: (attemptId: string, payload: ReviseAnswer) => Promise<boolean>
  onRepair: (payload: RepairLayout, caseIdsBefore: string[]) => Promise<boolean>
  repair: RepairState | null; onOpenCase: (caseId: string) => void
}) {
  const [mode, setMode] = useState<DeskMode>('view')
  const [expanded, setExpanded] = useState(false)
  const chain = useMemo(() => attemptChain(ws.attempts, ws.correction.current_attempt_id), [ws])
  const viewed = ws.attempts.find(a => a.record.id === attemptId) ?? currentAttempt(ws)
  const current = currentAttempt(ws)
  const questionAssets = ws.question.record.regions.map(region => region.asset_id)
  const [assetId, setAssetId] = useState<string | null>(null)
  const [questionDraft, setQuestionDraft] = useState<Region[]>([])
  const [stem, setStem] = useState(ws.question.record.stem_text ?? '')
  const [questionReason, setQuestionReason] = useState('')
  const [answerDraft, setAnswerDraft] = useState<Region[]>([])
  const [answerText, setAnswerText] = useState(current?.record.answer_text ?? '')
  const [unclear, setUnclear] = useState(current?.record.answer_text === null)
  const [answerReason, setAnswerReason] = useState('')
  const [groups, setGroups] = useState<ReplacementDraft[]>([{ regions: [], stem: '' }])
  const [activeGroup, setActiveGroup] = useState(0)
  const [supersede, setSupersede] = useState<Set<string>>(new Set())
  const [layoutReason, setLayoutReason] = useState('')
  const [questions, setQuestions] = useState<QuestionRead[] | null>(null)
  const [questionsError, setQuestionsError] = useState(false)

  useEffect(() => {
    const preferred = viewed?.record.asset_ids[0] ?? questionAssets[0]
    setAssetId(ws.assets.some(asset => asset.id === preferred) ? preferred : ws.assets[0]?.id ?? null)
    // Only an attempt switch moves the photo; reloads keep the teacher's page.
  }, [attemptId])
  useEffect(() => { setStem(ws.question.record.stem_text ?? ''); setQuestionDraft([]) }, [ws.question.record.question_id, ws.question.current_revision])
  useEffect(() => { setAnswerText(current?.record.answer_text ?? ''); setUnclear(current?.record.answer_text === null); setAnswerDraft([]) }, [current?.record.id, current?.current_revision])
  useEffect(() => {
    if (mode !== 'layout' || questions) return
    let live = true
    setQuestionsError(false)
    loadQuestions().then(list => { if (live) setQuestions(list) }).catch(() => { if (live) setQuestionsError(true) })
    return () => { live = false }
  }, [mode, questions, loadQuestions])
  useEffect(() => { setQuestions(null) }, [ws.homework.revision])

  const labels = new Map<string, string>()
  for (const asset of ws.assets) {
    const entry = chain.find(item => item.attempt.record.asset_ids.includes(asset.id) && item.attempt.record.id === viewed?.record.id)
    labels.set(asset.id, entry ? `${entry.label}照片` : questionAssets.includes(asset.id) ? '题目原图' : '作业其他页')
  }
  const locked = !!block || busy
  const editable = !locked && ws.correction.lifecycle === 'active'
  const questionOk = ws.question.currently_effective && ws.question.record.lifecycle === 'active'

  const layers: PhotoLayers = {
    question: ws.question.record.regions,
    answer: viewed?.record.answer_regions ?? [],
    others: mode === 'layout' && questions ? questions.filter(q => q.record.question_id !== ws.question.record.question_id).map((q, i) => ({ label: `其他题 ${i + 1}`, regions: q.record.regions })) : [],
    drafts: mode === 'question' ? [{ label: '新题区', regions: questionDraft, active: true }]
      : mode === 'answer' ? [{ label: '新答案区', regions: answerDraft, active: true }]
      : mode === 'layout' ? groups.map((group, i) => ({ label: `新题 ${i + 1}`, regions: group.regions, active: i === activeGroup })) : [],
  }
  const drawing: DrawingTool | null = !editable || mode === 'view' ? null
    : mode === 'question' ? { allowedAssets: null, hint: '在原图上拖动，框出这道题的完整范围（可框多块）。', onDraw: region => setQuestionDraft(list => [...list, region]) }
    : mode === 'answer' ? { allowedAssets: current?.record.asset_ids ?? [], hint: '在当前作答照片上拖动，框出孩子写答案的位置。', onDraw: region => setAnswerDraft(list => [...list, region]) }
    : { allowedAssets: null, hint: `正在为「新题 ${activeGroup + 1}」框选；一道题可以跨多块区域。`, onDraw: region => setGroups(list => list.map((group, i) => i === activeGroup ? { ...group, regions: [...group.regions, region] } : group)) }

  const questionRegions = questionDraft.length ? questionDraft : ws.question.record.regions
  const questionIssue = !questionOk ? '题目版本已变化，请刷新。' : !stem.trim() ? '题干文字不能为空。' : questionRegions.length === 0 ? '请框出题区。' : !questionReason.trim() ? '请写下修订原因。' : null
  const answerIssue = !current?.currently_effective ? '当前作答已变化，请刷新。' : !unclear && !answerText.trim() ? '请填写识别后的答案，或勾选“看不清/未作答”。' : !answerReason.trim() ? '请写下修订原因。' : null
  const chosen = (questions ?? []).filter(q => supersede.has(q.record.question_id))
  const layoutIssue = repairIssue(groups, layoutReason) ?? (questions === null ? '正在读取本作业的题目…' : null)
  const repairRunning = !!repair && repair.homeworkId === ws.homework.id && !isTerminal(repair.job.status)

  async function submitQuestion() {
    if (questionIssue || !editable) return
    if (await onReviseQuestion(ws.question.record.question_id, { expected_revision: ws.question.current_revision, stem_text: stem.trim(), regions: questionRegions, reason: questionReason.trim() })) { setQuestionReason(''); setQuestionDraft([]); setMode('view') }
  }
  async function submitAnswer() {
    if (answerIssue || !editable || !current) return
    if (await onReviseAnswer(current.record.id, { expected_revision: current.current_revision, answer_text: unclear ? null : answerText.trim(), answer_regions: answerDraft.length ? answerDraft : current.record.answer_regions, reason: answerReason.trim() })) { setAnswerReason(''); setAnswerDraft([]); setMode('view') }
  }
  async function submitLayout() {
    if (layoutIssue || !editable || repairRunning) return
    if (await onRepair(buildRepair(ws.homework, chosen, groups, layoutReason), ws.homework.case_ids)) { setGroups([{ regions: [], stem: '' }]); setActiveGroup(0); setSupersede(new Set()); setLayoutReason('') }
  }
  const toggle = (id: string) => setSupersede(old => { const next = new Set(old); if (next.has(id)) next.delete(id); else next.add(id); return next })

  return <section className={`document-panel ${expanded ? 'expanded-document' : ''}`} aria-label="作业原图与修订工具">
    <div className="document-heading"><div>
      <div className="breadcrumb">{studentName}<span>/</span>{ws.homework.title ?? '未命名作业'}<span>/</span>{sample ? '合成样例' : `题目版本 ${ws.question.record.revision}`}</div>
      <h2>{ws.question.record.stem_text ?? '题目文字待核实'}</h2>
    </div>{ws.correction.lifecycle !== 'active' && <span className="tag rose">已被替代</span>}</div>
    {ws.correction.lifecycle !== 'active' && <div className="superseded-note"><TriangleAlert size={16} /><div><p>此题已在补题/拆合中被替代，原记录仅供查看。</p>
      {ws.correction.replacement_case_ids.length > 0 ? <div className="button-row wrap">{ws.correction.replacement_case_ids.map((id, i) => <button key={id} className="button" onClick={() => onOpenCase(id)}>打开新题 {i + 1}</button>)}</div> : <small>新题仍在生成中，完成后会出现在这里。</small>}</div></div>}
    {focus && <div className={`help-quote ${focus.kind === 'appeal' ? 'appeal' : ''}`}><MessageCircle size={17} /><div><small>{kindLabels[focus.kind]} · 孩子说</small><p>“{focus.note}”</p></div></div>}

    <div className="mode-tabs" role="tablist" aria-label="工作方式">{modes.map(([value, text, Icon]) =>
      <button key={value} role="tab" aria-selected={mode === value} className={mode === value ? 'active' : ''} onClick={() => setMode(value)}><Icon size={15} />{text}</button>)}</div>

    <PhotoViewer assets={ws.assets} labels={labels} assetId={assetId} onAsset={setAssetId} loadImage={loadImage} layers={layers} drawing={drawing} expanded={expanded} onExpand={setExpanded} />

    {mode !== 'view' && <div className="tool-panel">
      {block && <div className="preview-note">{block}</div>}
      {mode === 'question' && <div className="tool-form">
        <p className="subtle">修订后，旧判断和提示立即失效，需要重新检查或人工核实。历史版本保留。</p>
        <label>题干文字<textarea value={stem} maxLength={16000} disabled={!editable} onChange={event => setStem(event.target.value)} /></label>
        <div className="field"><div className="field-title">题区<button className="text-link" disabled={!questionDraft.length} onClick={() => setQuestionDraft([])}>清空新框选</button></div>
          <RegionList regions={questionDraft} onRemove={i => setQuestionDraft(list => list.filter((_, j) => j !== i))} fallback={`沿用现有 ${ws.question.record.regions.length} 个题区；在原图上拖动即可重新框选。`} /></div>
        <label>修订原因<textarea value={questionReason} maxLength={1000} disabled={!editable} placeholder="例如：识别漏掉了第二行条件" onChange={event => setQuestionReason(event.target.value)} /></label>
        {questionIssue && editable && <small className="form-issue">{questionIssue}</small>}
        <button className="button primary" disabled={!editable || !!questionIssue} onClick={() => void submitQuestion()}>{busy ? <LoaderCircle size={15} className="spin" /> : <CheckCircle2 size={15} />}提交题目修订</button>
      </div>}
      {mode === 'answer' && <div className="tool-form">
        <p className="subtle">修订的是「{chain.find(item => item.current)?.label ?? '当前作答'}」的识别结果，不改动孩子的照片。修订后需重新检查或人工核实。</p>
        <label>识别后的答案<textarea value={answerText} maxLength={16000} disabled={!editable || unclear} onChange={event => setAnswerText(event.target.value)} /></label>
        <label className="check"><input type="checkbox" checked={unclear} disabled={!editable} onChange={event => setUnclear(event.target.checked)} />看不清或未作答（不填写答案文字）</label>
        <div className="field"><div className="field-title">答案区<button className="text-link" disabled={!answerDraft.length} onClick={() => setAnswerDraft([])}>清空新框选</button></div>
          <RegionList regions={answerDraft} onRemove={i => setAnswerDraft(list => list.filter((_, j) => j !== i))} fallback={`沿用现有 ${current?.record.answer_regions.length ?? 0} 个答案区；只能框在这次作答的照片上。`} /></div>
        <label>修订原因<textarea value={answerReason} maxLength={1000} disabled={!editable} placeholder="例如：把 6 识别成了 2" onChange={event => setAnswerReason(event.target.value)} /></label>
        {answerIssue && editable && <small className="form-issue">{answerIssue}</small>}
        <button className="button primary" disabled={!editable || !!answerIssue} onClick={() => void submitAnswer()}>{busy ? <LoaderCircle size={15} className="spin" /> : <CheckCircle2 size={15} />}提交答案修订</button>
      </div>}
      {mode === 'layout' && <div className="tool-form">
        <p className="subtle">漏题：不勾选原题，直接框出新题。拆分：勾选原题，框出多道新题。合并：勾选多道原题，框成一道新题。被替代的题保留历史，但立即停止引导孩子。</p>
        <div className="field"><div className="field-title">新题（{groups.length} / {MAX_REPLACEMENTS}）<button className="text-link" disabled={!editable || groups.length >= MAX_REPLACEMENTS} onClick={() => { setGroups(list => [...list, { regions: [], stem: '' }]); setActiveGroup(groups.length) }}><Plus size={13} />再加一道</button></div>
          {groups.map((group, i) => <div key={i} className={`replacement ${i === activeGroup ? 'active' : ''}`}>
            <div className="replacement-head"><button className="text-link strong" onClick={() => setActiveGroup(i)}>{i === activeGroup ? '● ' : '○ '}新题 {i + 1}</button><span className="subtle">{group.regions.length} 个区域</span>
              {groups.length > 1 && <button className="icon-button small" aria-label={`删除新题 ${i + 1}`} disabled={!editable} onClick={() => { setGroups(list => list.filter((_, j) => j !== i)); setActiveGroup(0) }}><Trash2 size={14} /></button>}</div>
            <RegionList regions={group.regions} onRemove={j => setGroups(list => list.map((item, k) => k === i ? { ...item, regions: item.regions.filter((_, m) => m !== j) } : item))} fallback={i === activeGroup ? '在原图上拖动框出这道题。' : '选中后再框选。'} />
            <input value={group.stem} maxLength={16000} disabled={!editable} placeholder="题干文字（可选，留空由系统识别）" aria-label={`新题 ${i + 1} 题干`} onChange={event => setGroups(list => list.map((item, k) => k === i ? { ...item, stem: event.target.value } : item))} />
          </div>)}</div>
        <div className="field"><div className="field-title">替代哪些现有题目（已选 {chosen.length}）</div>
          {questionsError ? <p className="warning-text">本作业题目读取失败，请刷新后再试。</p> : questions === null ? <p className="subtle"><LoaderCircle size={13} className="spin" /> 正在读取本作业的题目…</p> :
            <ul className="supersede-list">{questions.map(q => <li key={q.record.question_id}><label className="check"><input type="checkbox" disabled={!editable} checked={supersede.has(q.record.question_id)} onChange={() => toggle(q.record.question_id)} />
              <span>{q.record.stem_text ?? '（题干待识别）'}{q.record.question_id === ws.question.record.question_id && <span className="tag">当前题</span>}</span></label></li>)}
              {questions.length === 0 && <li className="subtle">本作业暂无有效题目。</li>}</ul>}</div>
        <label>原因<textarea value={layoutReason} maxLength={1500} disabled={!editable} placeholder="例如：第 2 题与第 3 题被切在了一起" onChange={event => setLayoutReason(event.target.value)} /></label>
        {layoutIssue && editable && (groups.some(group => group.regions.length > 0) || layoutReason) && <small className="form-issue">{layoutIssue}</small>}
        {repairRunning && <small className="form-issue">上一次补题/拆合仍在处理，完成后才能再次提交。</small>}
        <button className="button primary" disabled={!editable || !!layoutIssue || repairRunning} onClick={() => void submitLayout()}>{busy ? <LoaderCircle size={15} className="spin" /> : <Scissors size={15} />}提交补题 / 拆合</button>
      </div>}
    </div>}
    {repair && repair.homeworkId === ws.homework.id && <div className={`job-card job-${repair.job.status}`} role="status">
      {isTerminal(repair.job.status) ? repair.job.status === 'succeeded' ? <CheckCircle2 size={17} /> : <TriangleAlert size={17} /> : <LoaderCircle size={17} className="spin" />}
      <div><p>补题 / 拆合：{jobLabels[repair.job.status]}</p>
        {repair.job.status === 'succeeded' && (repair.createdCaseIds?.length ? <div className="button-row wrap">{repair.createdCaseIds.map((id, i) => <button key={id} className="button" onClick={() => onOpenCase(id)}>打开新题 {i + 1}</button>)}</div> : <small>新题已生成；如未显示，请刷新队列。</small>)}
        {repair.job.status === 'failed' && <small>{repair.job.failure?.message ?? '识别未能完成。'} 被替代的题不会恢复；可以不勾选原题、重新框选补题。</small>}
        {!isTerminal(repair.job.status) && <small>可以继续处理其他孩子，完成后这里会更新。</small>}</div>
    </div>}
  </section>
}
