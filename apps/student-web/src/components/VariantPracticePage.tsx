import { useCallback, useEffect, useState } from 'react'
import { ChevronRight, LoaderCircle, RefreshCw, Shuffle, Sparkles } from 'lucide-react'
import { safeError, StudentError, type PracticeSession, type VariantErrorGroup, type VariantPracticeOverview } from '../core'
import type { Workspace } from '../useStudentWorkspace'
import { RESUME_NOTICE, practiceMessage, statusLabels } from '../learning'

function VariantGroupCard({ group, disabled, busy, onStart }: { group: VariantErrorGroup; disabled: boolean; busy: boolean; onStart: (group: VariantErrorGroup) => void }) {
  const status = statusLabels[group.learning_status]
  return <article className="variant-card">
    <div className="variant-card-head">
      <div><span className="eyebrow">错题归纳</span><h2>{group.name}</h2></div>
      <span className={'status-pill ' + status.tone}>{status.label}</span>
    </div>
    <div className="variant-card-summary"><strong>最近错过 {group.error_count} 次</strong><span>{group.available_items > 0 ? '已有不同题面的变式题' : '变式题正在准备'}</span></div>
    <ul className="variant-question-list">
      {group.questions.slice(0, 3).map(question => <li key={question.case_id}><span className="variant-question-dot" /><span>{question.question_text || '题目文字暂缺'}</span></li>)}
    </ul>
    {group.questions.length > 3 && <p className="muted small">还有 {group.questions.length - 3} 道同类错题</p>}
    <button className="button primary full" disabled={disabled || busy || group.available_items < 1} onClick={() => onStart(group)}>
      {busy ? <LoaderCircle className="spin" size={17} /> : <Shuffle size={17} />}
      {group.available_items > 0 ? '换一道同类型题' : '等待变式题'}
      {group.available_items > 0 && <ChevronRight size={16} />}
    </button>
  </article>
}

export function VariantPracticePage({ workspace, onStarted }: { workspace: Workspace; onStarted: (session: PracticeSession, notice?: string) => void }) {
  const [overview, setOverview] = useState<VariantPracticeOverview | null>(null)
  const [active, setActive] = useState<PracticeSession | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const { client, studentId, recordVersion } = workspace
  const load = useCallback(async () => {
    setLoading(true); setError('')
    try {
      const [next, current] = await Promise.all([client.variantPractice(), client.activePractice()])
      setOverview(next); setActive(current?.purpose === 'variant' ? current : null)
    } catch (e) {
      setOverview(null); setActive(null); setError(safeError(e).message); workspace.report(e)
    } finally { setLoading(false) }
  }, [client, studentId, recordVersion])
  useEffect(() => { void load() }, [load])

  const start = async (group: VariantErrorGroup) => {
    if (busy) return
    setBusy(group.knowledge_id); setError('')
    try {
      onStarted(await client.startPractice({ purpose: 'variant', willing: true, knowledge_id: group.knowledge_id, count: 1, source_case_id: group.source_case_id }))
    } catch (e) {
      const resumable = e instanceof StudentError && e.status === 409 && e.code === 'practice_active'
      const current = resumable ? await client.activePractice().catch(() => null) : null
      if (current) onStarted(current, RESUME_NOTICE)
      else { setError(practiceMessage(e, safeError(e).message)); workspace.report(e); await load() }
    } finally { setBusy('') }
  }

  return <section className="variant-page" aria-labelledby="variant-page-title">
    <div className="page-title-row"><div><span className="eyebrow">从错题里再想一次</span><h1 id="variant-page-title">举一反三</h1><p>按知识点归纳你最近做错的题，再做一道题面和选项都不同的同类型题。</p></div><button className="button secondary" disabled={loading || !!workspace.busy} onClick={() => void load()}><RefreshCw size={17} /> 刷新归纳</button></div>
    {overview?.projection.pending && <div className="variant-sync" role="status"><LoaderCircle className="spin" size={17} /><span>AI刚更新了批改，错题归纳和知识地图正在同步。</span></div>}
    {overview && !overview.projection.pending && <div className="variant-sync ready" role="status"><Sparkles size={17} /><span>本次错题归纳已同步到你的知识地图。</span></div>}
    {active && <div className="continue-banner"><div><strong>还有一道变式题没有完成</strong><p className="muted">可以继续上次的举一反三练习。</p></div><button className="button primary" onClick={() => onStarted(active, RESUME_NOTICE)}>继续练习 <ChevronRight size={17} /></button></div>}
    {error && <div className="inline-error" role="alert"><p>{error}</p><button className="text-button" onClick={() => void load()}><RefreshCw size={15} /> 再试一次</button></div>}
    {loading ? <div className="variant-empty" role="status"><LoaderCircle className="spin" size={27} /><p>正在整理你的错题类型…</p></div>
      : overview && overview.groups.length ? <><div className="variant-grid">{overview.groups.map(group => <VariantGroupCard key={group.knowledge_id} group={group} disabled={!!active || !!workspace.busy} busy={busy === group.knowledge_id} onStart={groupToStart => void start(groupToStart)} />)}</div>{overview.pending_count > 0 && <p className="muted small variant-pending-note">还有 {overview.pending_count} 道题目前识别不确定，暂不纳入错题归纳。</p>}</>
      : overview && overview.pending_count > 0 ? <div className="variant-empty"><span className="ready-symbol"><LoaderCircle size={23} /></span><h2>还有题目需要确认</h2><p>已识别到 {overview.pending_count} 道题，但 AI 目前还不能可靠判断对错。</p></div>
      : <div className="variant-empty"><span className="ready-symbol"><Sparkles size={23} /></span><h2>还没有可归纳的错题</h2><p>完成作业后，AI 判定为错误或部分正确的题会自动出现在这里。</p></div>}
    {overview && <p className="muted small variant-notice">{overview.notice}</p>}
  </section>
}
