import { useState } from 'react'
import { ArrowRight, FileImage, Trash2 } from 'lucide-react'
import type { Workspace } from '../useStudentWorkspace'
import { dateLabel, stageLabels, uploadState } from '../presentation'

/** Unfinished drafts kept on this device, so a reload, a closed tab or an expired sign-in can pick up where it stopped. */
export function ResumeDrafts({ workspace, onOpen }: { workspace: Workspace; onOpen: (id: string) => void }) {
  const [confirming, setConfirming] = useState<string | null>(null)
  const items = workspace.drafts.filter(d => !d.id.startsWith('remote_') && d.stage !== 'complete').slice(0, 4)
  if (!items.length) return null
  return <section className="resume-drafts" aria-labelledby="resume-drafts-title">
    <div className="resume-head"><h2 id="resume-drafts-title">上次没做完的</h2><p className="muted small">这些草稿保存在这台设备上。标着“还在这台设备”的照片，服务器还没有收到。</p></div>
    <ul>{items.map(item => { const state = uploadState(item), sent = item.homeworkId != null || item.uploadedCount > 0 || item.stage !== 'local'; return <li key={item.id}>
      <span className="history-paper small-paper" aria-hidden="true"><FileImage size={20} strokeWidth={1.4} /></span>
      <span className="resume-info"><strong>{item.title}</strong><span className="muted small"><time dateTime={item.createdAt}>{dateLabel(item.createdAt)}</time>{item.pageCount > 1 ? ` · ${item.pageCount} 页` : ''} · {stageLabels[item.stage]}</span><span className={`status-pill ${state.tone}`}>{state.label}</span></span>
      {confirming === item.id ? <span className="resume-actions" role="group" aria-label="确认删除草稿"><button className="button quiet" disabled={!!workspace.busy} onClick={() => setConfirming(null)}>先不删</button><button className="button danger" disabled={!!workspace.busy} onClick={() => { void workspace.discardDraft(item.id).then(() => setConfirming(null)) }}><Trash2 size={15} /> 删除</button></span>
        : <span className="resume-actions">{!sent && <button className="icon-button" disabled={!!workspace.busy} aria-label={`删除草稿${item.title}`} title="删除这份草稿" onClick={() => setConfirming(item.id)}><Trash2 size={16} /></button>}<button className="button secondary" disabled={!!workspace.busy} onClick={() => onOpen(item.id)}>继续 <ArrowRight size={16} /></button></span>}
    </li> })}</ul>
  </section>
}
