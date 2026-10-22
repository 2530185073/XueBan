import { useEffect, useRef, useState } from 'react'
import { ArrowLeft, Expand, History, LoaderCircle, RefreshCw, X } from 'lucide-react'
import type { Workspace } from '../useStudentWorkspace'
import { EvidenceTraceUnavailable, evidenceFocusKey, loadEvidenceTrace, type EvidenceFocus, type EvidenceTrace } from '../core/evidenceTrace'
import { attemptLabels, verdictLabels } from '../timeline'
import { dateLabel } from '../presentation'

/** Own the exact historical asset and revoke it when this selection/session ends. */
function EvidencePhoto({ workspace, assetId, scope, index }: { workspace: Workspace; assetId: string; scope: string; index: number }) {
  const [state, setState] = useState<{ url: string | null; failed: boolean }>({ url: null, failed: false })
  const [expanded, setExpanded] = useState(false)
  useEffect(() => {
    let live = true, owned: string | null = null
    setState({ url: null, failed: false }); setExpanded(false)
    workspace.client.assetBlob(assetId).then(blob => {
      if (!live || workspace.client.evidenceScope !== scope) return
      owned = workspace.client.objectUrl(blob); setState({ url: owned, failed: false })
    }, () => { if (live && workspace.client.evidenceScope === scope) setState({ url: null, failed: true }) })
    return () => { live = false; if (owned) workspace.client.releaseObjectUrl(owned) }
  }, [assetId, scope, workspace.client])
  const url = workspace.client.evidenceScope === scope ? state.url : null
  return <figure className="evidence-photo">
    <figcaption>当时的原照片 · {index + 1}<button className="text-button" disabled={!url} onClick={() => setExpanded(true)}><Expand size={15} /> 放大原图</button></figcaption>
    {url ? <img src={url} alt={`这条学习记录对应的原照片 ${index + 1}`} /> : <p className="muted small" role="status">{state.failed ? '这张历史照片暂时无法打开，未改用其他照片。' : '正在读取当时的原照片…'}</p>}
    {expanded && url && <div className="image-dialog evidence-image-dialog" role="dialog" aria-modal="true" aria-label="这条学习记录的原照片" onClick={() => setExpanded(false)} onKeyDown={event => { if (event.key === 'Escape') setExpanded(false); if (event.key === 'Tab') { event.preventDefault(); event.currentTarget.querySelector<HTMLButtonElement>('button')?.focus() } }}>
      <button className="icon-button" autoFocus aria-label="关闭历史原图" onClick={() => setExpanded(false)}><X /></button>
      <img src={url} alt={`当时的原照片 ${index + 1}，未裁剪`} onClick={event => event.stopPropagation()} />
    </div>}
  </figure>
}

export function EvidenceTraceContent({ trace, workspace, scope }: { trace: EvidenceTrace; workspace: Workspace; scope: string }) {
  const { attempt, decision, question, evidence, record } = trace
  const label = attemptLabels(record.attempts.map(a => a.record)).get(attempt.id) ?? '作答'
  const result = decision.origin === 'staff' ? '当时的老师判断' : decision.origin === 'rule' ? '当时的规则检查' : '当时的 AI 初判'
  return <div className="evidence-trace-grid">
    <div className="evidence-trace-text" data-attempt-id={attempt.id} data-decision-id={decision.id}>
      <span className="status-pill green">已定位 · {label}</span><time dateTime={attempt.occurred_at}>{dateLabel(attempt.occurred_at)}</time>
      <section><h3>当时拍下的题面</h3><p>{question.stem_text ?? '该版本未保存清楚的题干文字，请核对原照片。'}</p>
        {!!question.options?.length && <ol className="evidence-trace-options">{question.options!.map((o, i) => <li key={i}><span>{o.label}</span><p>{o.text}</p></li>)}</ol>}
      </section>
      <section className="evidence-original-answer"><h3>{label} · 当时保存的识读原文</h3><p>{attempt.answer_text ?? '该版本没有保存作答文字，不据此认定未作答。'}</p><small>文字可能与笔迹不同，请以这次作答的原图为准。</small></section>
      <section><h3>{result}</h3><p>{verdictLabels[decision.verdict]}</p><p className="muted small">{evidence.assistance_level === 'direction' ? '这次作答看过方向提示。' : evidence.assistance_level === 'none_reported' ? '这条记录未报告使用帮助。' : '帮助情况以这条学习记录为准。'} 此处只回看当时记录，不代替当前结果，也不表示已经独立掌握。</p></section>
    </div>
    <div className="evidence-trace-photos">{attempt.asset_ids.map((id, index) => <EvidencePhoto key={`${scope}:${id}`} workspace={workspace} assetId={id} scope={scope} index={index} />)}</div>
  </div>
}

/** A read-only destination; never substitutes the current case/photo for old evidence. */
export function EvidenceTracePanel({ focus, workspace, onClose }: { focus: EvidenceFocus; workspace: Workspace; onClose: () => void }) {
  const [reload, setReload] = useState(0)
  const key = `${evidenceFocusKey(focus)}:${workspace.recordVersion}:${reload}`, heading = useRef<HTMLHeadingElement>(null)
  const [state, setState] = useState<{ key: string; trace?: EvidenceTrace; error?: string }>({ key })
  useEffect(() => {
    let live = true; setState({ key }); heading.current?.focus()
    if (workspace.client.evidenceScope !== focus.scope || !workspace.studentId) return
    loadEvidenceTrace(workspace.client, focus, workspace.studentId).then(trace => {
      if (live && workspace.client.evidenceScope === focus.scope) setState({ key, trace })
    }, error => { if (live && workspace.client.evidenceScope === focus.scope) setState({ key, error: error instanceof EvidenceTraceUnavailable ? error.message : '这条记录暂时没有读全。可重新读取；不会用当前作答代替。' }) })
    return () => { live = false }
  }, [key, reload, workspace.client, workspace.studentId, workspace.recordVersion])
  const current = state.key === key && workspace.client.evidenceScope === focus.scope ? state : null
  return <section className="evidence-trace" aria-labelledby="evidence-trace-heading">
    <header><div><span className="eyebrow">从学习记录，回到这一次作答</span><h2 id="evidence-trace-heading" ref={heading} tabIndex={-1}>这条学习记录</h2></div><button className="text-button" onClick={onClose}><ArrowLeft size={16} /> 返回当前作业</button></header>
    {current?.trace ? <EvidenceTraceContent trace={current.trace} workspace={workspace} scope={focus.scope} /> : current?.error ? <div className="evidence-trace-message" role="status"><History size={24} /><p>{current.error}</p><button className="button quiet" onClick={() => setReload(v => v + 1)}><RefreshCw size={15} /> 重新读取记录</button></div> : <div className="evidence-trace-message" role="status"><LoaderCircle className="spin" size={24} /><p>正在核对这次作答与原照片…</p></div>}
  </section>
}
