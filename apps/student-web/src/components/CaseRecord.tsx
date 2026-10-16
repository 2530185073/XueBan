import { useEffect, useId, useState } from 'react'
import { History, ImageOff, LoaderCircle, MessageSquareWarning, RefreshCw } from 'lucide-react'
import { canAppeal, openHelp, safeError } from '../core'
import type { CaseDetail, CaseRecord, HelpRequest } from '../core/types'
import type { Workspace } from '../useStudentWorkspace'
import { buildTimeline, currentHelp, helpKindLabels, helpStatusLabels, type TimelineEntry } from '../timeline'
import { dateLabel } from '../presentation'
import { useAssetBlob, useObjectUrl } from './useObjectUrl'

const APPEAL_NOTE_LIMIT = 200

export function HelpStatusList({ items }: { items: HelpRequest[] }) {
  if (!items.length) return null
  return <ul className="help-status-list" aria-label="求助与重查进度">{items.map(h => { const status = helpStatusLabels[h.status]; return <li key={h.id}>
    <div className="help-status-head"><strong>{helpKindLabels[h.kind]}</strong><span className={`status-pill ${status.tone}`}>{status.label}</span></div>
    <p className="muted">{h.note}</p>
    {h.resolution_note && (h.status === 'resolved' || h.status === 'unresolved') && <p className="help-resolution"><span>老师说明</span>{h.resolution_note}</p>}
    <time dateTime={h.created_at}>{dateLabel(h.created_at)} 提交</time>
  </li> })}</ul>
}

export function AppealForm({ disabled, onSubmit, onCancel }: { disabled: boolean; onSubmit: (note: string) => void; onCancel: () => void }) {
  const [note, setNote] = useState(''), id = useId()
  return <form className="appeal-form" onSubmit={event => { event.preventDefault(); onSubmit(note) }}>
    <label htmlFor={id}>想请老师重查的原因<span className="muted">（可不填）</span></label>
    <textarea id={id} autoFocus rows={3} maxLength={APPEAL_NOTE_LIMIT} value={note} placeholder="例如：我觉得第二步的计算是对的。" aria-describedby={`${id}-count`} onChange={event => setNote(event.target.value)} />
    <div className="appeal-form-foot"><span id={`${id}-count`} className="muted small">{[...note].length}/{APPEAL_NOTE_LIMIT} · 老师处理前，当前结果保持不变</span><span className="appeal-form-actions"><button type="button" className="button quiet" disabled={disabled} onClick={onCancel}>取消</button><button type="submit" className="button secondary" disabled={disabled}>提交重查申请</button></span></div>
  </form>
}

function Thumbnail({ workspace, assetId, label }: { workspace: Workspace; assetId: string; label: string }) {
  const { blob, loading, failed } = useAssetBlob(workspace.client, assetId, workspace.studentId)
  const url = useObjectUrl(workspace.client, blob)
  return <span className="timeline-thumb">{url ? <img src={url} alt={`${label}的照片`} /> : loading ? <LoaderCircle className="spin" size={16} aria-label="正在打开照片" /> : failed ? <ImageOff size={16} aria-label="照片暂时打不开" /> : null}</span>
}

export function Timeline({ entries, workspace }: { entries: TimelineEntry[]; workspace?: Workspace }) {
  if (!entries.length) return <p className="muted small">还没有可以显示的记录。</p>
  return <ol className="timeline">{entries.map(entry => <li key={entry.key} className={`timeline-item ${entry.kind} ${entry.state}`}>
    <span className="timeline-dot" aria-hidden="true" />
    <div className="timeline-body">
      <div className="timeline-head">
        {entry.state === 'invalid' ? <del className="timeline-title">{entry.title}</del> : <strong className="timeline-title">{entry.title}</strong>}
        {entry.badges.map(badge => <span key={badge.label} className={`status-pill ${badge.tone}`}>{badge.label}</span>)}
      </div>
      {entry.note && <p className="timeline-note">{entry.note}</p>}
      {entry.text && (entry.state === 'invalid' ? <p className="timeline-text"><del>{entry.text}</del></p> : <p className="timeline-text">{entry.text}</p>)}
      <time dateTime={entry.at}>{dateLabel(entry.at)}</time>
    </div>
    {entry.assetId && workspace && <Thumbnail workspace={workspace} assetId={entry.assetId} label={entry.title} />}
  </li>)}</ol>
}

/** Help/appeal status plus the per-case 订正记录, re-read after every write or refresh. */
export function CaseRecordPanel({ row, workspace }: { row: CaseDetail; workspace: Workspace }) {
  const [record, setRecord] = useState<CaseRecord | null>(null)
  const [loadError, setLoadError] = useState('')
  const [reload, setReload] = useState(0)
  const [appealing, setAppealing] = useState(false)
  const caseId = row.case.id, revision = row.case.revision
  useEffect(() => {
    let live = true; setLoadError('')
    workspace.client.caseRecord(caseId).then(value => { if (live) setRecord(value) }, (e: unknown) => { if (live) { setRecord(null); setLoadError(safeError(e).message) } })
    return () => { live = false }
  }, [caseId, revision, reload, workspace.client, workspace.recordVersion])
  const fresh = record?.case.id === caseId ? record : null
  const help = fresh ? currentHelp(fresh) : [], entries = fresh ? buildTimeline(fresh) : null
  const appealOpen = help.some(h => h.kind === 'appeal' && h.attempt_id === row.attempt.id && openHelp(h))
  const appealable = canAppeal(row) && !appealOpen
  return <>
    <section className="help-panel" aria-labelledby={`${caseId}-help`}>
      <div className="section-label"><MessageSquareWarning size={18} /><h3 id={`${caseId}-help`}>求助与重查</h3></div>
      <div aria-live="polite"><HelpStatusList items={help} /></div>
      {appealable && !appealing && <div className="appeal-entry"><p className="muted small">觉得这次判断不对？可以请坐班老师重新看一遍。</p><button className="button secondary" disabled={!!workspace.busy} onClick={() => setAppealing(true)}>申请老师重查</button></div>}
      {appealable && appealing && <AppealForm disabled={!!workspace.busy} onCancel={() => setAppealing(false)} onSubmit={note => { void workspace.requestHelp(row, 'appeal', note).then(ok => { if (ok) setAppealing(false) }) }} />}
      {appealOpen && <p className="muted small">已申请老师重查。老师处理前，当前结果保持不变。</p>}
      {!canAppeal(row) && !help.length && <p className="muted small">检查有结果后，如果觉得判断不对，可以在这里申请老师重查。</p>}
    </section>
    <details className="case-history" open>
      <summary><History size={17} /> 订正记录{entries ? `（${entries.length} 条）` : ''}</summary>
      <p className="muted small">按时间排列。当前结果以上方状态为准；已失效的判断只作记录，不再算数。</p>
      {loadError ? <div className="inline-error" role="alert"><p>{loadError}</p><button className="text-button" onClick={() => setReload(v => v + 1)}><RefreshCw size={14} /> 重新读取记录</button></div> : entries ? <Timeline entries={entries} workspace={workspace} /> : <p className="muted small"><LoaderCircle className="spin" size={14} /> 正在读取订正记录…</p>}
    </details>
  </>
}
