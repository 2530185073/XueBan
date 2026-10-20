import { useCallback, useEffect, useRef, useState } from 'react'
import { ChevronRight, LoaderCircle, RefreshCw, Shuffle, Sparkles } from 'lucide-react'
import { safeError, StudentError, type PracticeSession, type VariantErrorGroup, type VariantGeneration, type VariantPracticeOverview } from '../core'
import type { Workspace } from '../useStudentWorkspace'
import { RESUME_NOTICE, practiceMessage, statusLabels } from '../learning'

export const generationPending = (job: VariantGeneration) => ['queued', 'generating', 'verifying'].includes(job.status)
export function generationCopy(job: VariantGeneration): string {
  if (job.session_id) return '这道题已经开始过，作答记录已保留。'
  switch (job.status) {
    case 'queued': return '已收到你的选择，正在等待生成。'
    case 'generating': return 'AI 正在围绕原题，生成一道同题型题。'
    case 'verifying': return '题目已生成，AI 正在独立核对题意、答案和题型。'
    case 'ready': return '题目已通过 AI 核验，可以开始。'
    case 'superseded': return '原题或教材记录已更新，请从最新的错题重新选择。'
    case 'failed': return job.failure_code === 'verification_failed' ? '这次生成的题目未通过核验，暂不提供作答。' : job.failure_code === 'unsupported_source_type' ? '这道原题的题型暂时还不能可靠识别，可以先回到作业。' : '这次还没能准备好题目，可以稍后重试。'
  }
}

export function VariantGenerationCard({ job, sourceText, busy, disabled, onOpen, onRetry }: { job: VariantGeneration; sourceText?: string; busy: boolean; disabled: boolean; onOpen: () => void; onRetry: () => void }) {
  const pending = generationPending(job)
  const retryable = job.status === 'failed' && !['unsupported_source_type', 'stale_source', 'source_not_eligible', 'curriculum_mismatch'].includes(job.failure_code ?? '')
  return <article className="variant-generation" aria-label="变式题准备情况">
    <div className="variant-generation-heading"><span className="eyebrow">你选择的一道题</span>{pending && <LoaderCircle className="spin" size={17} />}</div>
    {sourceText && <p className="variant-generation-source">原题：{sourceText}</p>}
    <p role="status">{generationCopy(job)}</p>
    {pending && <p className="muted small">等待不占用作答时间，可以先去做自己的作业。刷新后也能找回。</p>}
    {job.status === 'ready' && !job.session_id && <p className="muted small">AI 核验供参考；想试试时再开始，也可以先休息。</p>}
    <div className="hint-actions">
      {(job.status === 'ready' || job.session_id) && <button className="button primary" disabled={busy || (disabled && !job.session_id)} onClick={onOpen}>{busy ? <LoaderCircle className="spin" size={17} /> : <ChevronRight size={17} />}{job.session_id ? '查看这次作答' : '开始这道题'}</button>}
      {retryable && <button className="button secondary" disabled={disabled || busy} onClick={onRetry}><RefreshCw size={16} /> 再生成一次</button>}
    </div>
  </article>
}

export function VariantGroupCard({ group, disabled, busy, generation, onStart }: { group: VariantErrorGroup; disabled: boolean; busy: boolean; generation?: VariantGeneration; onStart: (group: VariantErrorGroup) => void }) {
  const status = statusLabels[group.learning_status]
  const historical = group.curriculum_status === 'historical_unmatched'
  const pending = generation && generationPending(generation)
  const prepared = generation?.status === 'ready' && !generation.session_id
  const availability = historical ? '保留原教材作答记录' : pending ? generationCopy(generation) : prepared ? '同题型题已准备好' : '想再试试时，由 AI 生成并核验一道'
  return <article className="variant-card">
    <div className="variant-card-head"><div><span className="eyebrow">错题归纳</span><h2>{group.name}</h2></div><span className={'status-pill ' + status.tone}>{status.label}</span></div>
    <div className="variant-card-summary"><strong>有 {group.error_count} 道题可以再看看</strong><span>{availability}</span></div>
    <ul className="variant-question-list">{group.questions.slice(0, 3).map(question => <li key={question.case_id}><span className="variant-question-dot" /><span>{question.question_text || '题目文字暂缺'}</span></li>)}</ul>
    {group.questions.length > 3 && <p className="muted small">还有 {group.questions.length - 3} 道同类错题</p>}
    {historical ? <p className="muted small">这道题的教材与当前选择不同，暂不用于当前教材的练习。</p> : group.graph_version === null && <p className="muted small">知识点关联仍在整理，未确认的关联不会计入知识图谱。</p>}
    <button className="button primary full" disabled={disabled || busy || historical || !!pending} onClick={() => onStart(group)}>
      {busy || pending ? <LoaderCircle className="spin" size={17} /> : <Shuffle size={17} />}
      {historical ? '原教材错题' : pending ? '准备中，可先做自己的作业' : prepared ? '开始已准备好的题' : generation?.session_id ? '查看这次作答' : '生成一道同题型'}
      {!historical && !pending && <ChevronRight size={16} />}
    </button>
  </article>
}

export function VariantPracticePage({ workspace, onStarted, initialGeneration }: { workspace: Workspace; onStarted: (session: PracticeSession, notice?: string) => void; initialGeneration?: VariantGeneration | null }) {
  const [overview, setOverview] = useState<VariantPracticeOverview | null>(null)
  const [jobs, setJobs] = useState<VariantGeneration[]>(() => initialGeneration ? [initialGeneration] : [])
  const [active, setActive] = useState<PracticeSession | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const mounted = useRef(false), busyRef = useRef(false), loadVersion = useRef(0)
  const { client, studentId, recordVersion } = workspace
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; loadVersion.current++ } }, [])
  const load = useCallback(async (clearError = true) => {
    const version = ++loadVersion.current
    setLoading(true); if (clearError) setError('')
    try {
      const [next, current, generations] = await Promise.all([client.variantPractice(), client.activePractice(), client.variantGenerations()])
      if (!mounted.current || version !== loadVersion.current) return
      setOverview(next); setActive(current); setJobs(generations.items)
    } catch (e) {
      if (!mounted.current || version !== loadVersion.current) return
      setError(practiceMessage(e, safeError(e).message)); workspace.report(e)
    } finally { if (mounted.current && version === loadVersion.current) setLoading(false) }
  }, [client, studentId, recordVersion])
  useEffect(() => { void load() }, [load])
  const pending = jobs.some(generationPending)
  useEffect(() => {
    if (!pending) return
    let cancelled = false
    let timer: ReturnType<typeof setTimeout>
    const poll = async () => {
      const version = loadVersion.current
      try {
        const result = await client.variantGenerations()
        if (!cancelled && mounted.current && version === loadVersion.current) setJobs(result.items)
      } catch (e) { if (!cancelled && mounted.current) setError(practiceMessage(e, safeError(e).message)) }
      if (!cancelled) timer = setTimeout(() => void poll(), 5000)
    }
    timer = setTimeout(() => void poll(), 2500)
    return () => { cancelled = true; clearTimeout(timer) }
  }, [client, pending])

  const act = async (key: string, task: () => Promise<void>) => {
    if (busyRef.current) return
    busyRef.current = true; setBusy(key); setError('')
    try { await task() }
    catch (e) {
      if (!mounted.current) return
      const resumable = e instanceof StudentError && e.status === 409 && e.code === 'practice_active'
      const current = resumable ? await client.activePractice().catch(() => null) : null
      if (!mounted.current) return
      if (current) onStarted(current, RESUME_NOTICE)
      else { setError(practiceMessage(e, safeError(e).message)); workspace.report(e); await load(false) }
    } finally { busyRef.current = false; if (mounted.current) setBusy('') }
  }
  const open = (job: VariantGeneration) => act(job.source_case_id, async () => {
    const session = job.session_id ? await client.practiceSession(job.session_id) : await client.startPractice({ purpose: 'variant', willing: true, generation_job_id: job.job_id, count: 1 })
    if (mounted.current) onStarted(session)
  })
  const generate = (caseId: string, knowledgeId?: string) => act(caseId, async () => {
    const job = await client.generateVariant({ willing: true, source_case_id: caseId, count: 1, ...(knowledgeId ? { knowledge_id: knowledgeId } : {}) })
    if (mounted.current) {
      // An older poll must not erase the newly accepted task and stop its polling.
      loadVersion.current++
      setJobs(old => [job, ...old.filter(item => item.job_id !== job.job_id && item.source_case_id !== job.source_case_id)])
    }
  })
  const jobFor = (group: VariantErrorGroup) => jobs.find(job => job.source_case_id === group.source_case_id)
  const start = (group: VariantErrorGroup) => {
    const job = jobFor(group)
    if (job && (job.status === 'ready' || job.session_id)) void open(job)
    else void generate(group.source_case_id, group.graph_version === null ? undefined : group.knowledge_id)
  }
  // The API sorts newest first. Keep one current task per original question; no generation on reads.
  const recentJobs = jobs.filter((job, index) => jobs.findIndex(item => item.source_case_id === job.source_case_id) === index)
  const sourceText = (job: VariantGeneration) => overview?.groups.flatMap(group => group.questions).find(question => question.case_id === job.source_case_id)?.question_text ?? undefined

  return <section className="variant-page" aria-labelledby="variant-page-title">
    <div className="page-title-row"><div><span className="eyebrow">从错题里再想一次</span><h1 id="variant-page-title">举一反三</h1><p>想再试试时，选一道原题，让 AI 生成并核验一道同题型题。只做一道，随时可以结束。</p></div><button className="button secondary" disabled={loading || !!busy || !!workspace.busy} onClick={() => void load()}><RefreshCw size={17} /> 刷新归纳</button></div>
    {overview?.projection.pending && <div className="variant-sync" role="status"><LoaderCircle className="spin" size={17} /><span>AI 刚更新了批改，错题归纳和知识地图正在同步。</span></div>}
    {overview && !overview.projection.pending && <div className="variant-sync ready" role="status"><Sparkles size={17} /><span>可核验的错题记录已归纳，知识点关联会保留自己的依据。</span></div>}
    {active && <div className="continue-banner"><div><strong>上次的活动还没有结束</strong><p className="muted">可以继续上次的{active.purpose === 'variant' ? '举一反三' : '预习'}，也可以随时结束。</p></div><button className="button primary" onClick={() => onStarted(active, RESUME_NOTICE)}>继续上次活动 <ChevronRight size={17} /></button></div>}
    {error && <div className="inline-error" role="alert"><p>{error}</p><button className="text-button" disabled={!!busy || loading} onClick={() => void load()}><RefreshCw size={15} /> 刷新状态</button></div>}
    {recentJobs.length > 0 && <section className="variant-generations" aria-label="我选择的变式题">{recentJobs.map(job => <VariantGenerationCard key={job.job_id} job={job} sourceText={sourceText(job)} busy={busy === job.source_case_id} disabled={!!active || !!busy || !!workspace.busy} onOpen={() => void open(job)} onRetry={() => void generate(job.source_case_id, job.knowledge_id ?? undefined)} />)}</section>}
    {loading ? <div className="variant-empty" role="status"><LoaderCircle className="spin" size={27} /><p>正在整理你的错题类型…</p></div>
      : overview && overview.groups.length ? <><div className="variant-grid">{overview.groups.map(group => <VariantGroupCard key={group.knowledge_id} group={group} generation={jobFor(group)} disabled={!!active || !!busy || !!workspace.busy} busy={busy === group.source_case_id} onStart={start} />)}</div>{overview.pending_count > 0 && <p className="muted small variant-pending-note">还有 {overview.pending_count} 道题目前识别不确定，暂不纳入错题归纳。</p>}</>
      : overview && overview.pending_count > 0 ? <div className="variant-empty"><span className="ready-symbol"><LoaderCircle size={23} /></span><h2>还有题目需要确认</h2><p>已识别到 {overview.pending_count} 道题，但 AI 目前还不能可靠判断对错。</p></div>
      : error && !overview ? <div className="variant-empty"><h2>错题归纳暂时没有加载</h2><p>可以重试，也可以先回到自己的作业。</p></div>
      : <div className="variant-empty"><span className="ready-symbol"><Sparkles size={23} /></span><h2>还没有可归纳的错题</h2><p>完成作业后，AI 判定为错误或部分正确的题会自动出现在这里。</p></div>}
    {overview && <p className="muted small variant-notice">{overview.notice}</p>}
  </section>
}
