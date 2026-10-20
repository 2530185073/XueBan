import { useEffect, useState } from 'react'
import { ChevronLeft, ChevronRight, Expand, ImageOff, LoaderCircle, RefreshCw, X } from 'lucide-react'
import { pageLabel } from '../core'
import type { CaseDetail, DraftView, HomeworkDetail } from '../core/types'
import type { Workspace } from '../useStudentWorkspace'
import { useObjectUrl } from './useObjectUrl'

/** One viewable photo: a homework page, or a correction photo that is not a page. */
export interface PageSource { key: string; label: string; assetId?: string; blob?: Blob; localId?: string; uploaded?: boolean }

/**
 * Homework pages first (local copies when this device still has them), then
 * the selected answer's own photo and an unsent correction photo.
 */
export function pageSources(draft: DraftView | null, serverPages: string[] | null, row?: CaseDetail): { sources: PageSource[]; pageCount: number } {
  const local = draft && !draft.correction ? draft.pages : []
  const sources: PageSource[] = local.length ? local.map((p, i) => ({ key: p.id, label: `第 ${i + 1} 页`, assetId: p.assetId, blob: p.blob, localId: p.id, uploaded: p.uploaded })) : (serverPages ?? []).map((id, i) => ({ key: id, label: `第 ${i + 1} 页`, assetId: id }))
  const pageCount = sources.length
  for (const id of row?.attempt.asset_ids ?? []) if (!sources.some(s => s.assetId === id)) sources.push({ key: id, label: row?.attempt.parent_attempt_id ? '订正照片' : '作答照片', assetId: id })
  if (draft?.correction) for (const p of draft.pages) if (!p.assetId || !sources.some(s => s.assetId === p.assetId)) sources.push({ key: p.id, label: '这次订正照片', assetId: p.assetId, blob: p.blob, localId: p.id, uploaded: p.uploaded })
  return { sources, pageCount }
}

export function PageNav({ sources, pageCount, index, onSelect }: { sources: PageSource[]; pageCount: number; index: number; onSelect: (index: number) => void }) {
  if (sources.length < 2) return null
  return <div className="page-nav" role="group" aria-label="翻看每一页">
    <button type="button" className="icon-button" disabled={index === 0} aria-label="上一页" onClick={() => onSelect(index - 1)}><ChevronLeft size={18} /></button>
    <span className="page-count" aria-live="polite">{index < pageCount ? pageLabel(index, pageCount) : sources[index]?.label}</span>
    <button type="button" className="icon-button" disabled={index >= sources.length - 1} aria-label="下一页" onClick={() => onSelect(index + 1)}><ChevronRight size={18} /></button>
    <span className="page-tabs">{sources.map((s, i) => <button type="button" key={s.key} className={i === index ? 'active' : ''} aria-pressed={i === index} onClick={() => onSelect(i)}>{i < pageCount ? i + 1 : s.label}</button>)}</span>
  </div>
}

/** Ordered original pages of an opened homework; falls back to the listed assets if they cannot be read. */
function useHomeworkPages(workspace: Workspace, detail: HomeworkDetail | null) {
  const [pages, setPages] = useState<{ key: string; ids: string[] } | null>(null)
  const key = detail ? `${detail.homework.id}:${detail.homework.revision}:${detail.jobs.map(j => j.id).join(',')}:${detail.cases.map(r => r.attempt.id).join(',')}` : ''
  useEffect(() => {
    if (!detail) { setPages(null); return }
    let live = true
    workspace.client.homeworkPages(detail).then(ids => { if (live) setPages({ key, ids }) }, () => { if (live) setPages({ key, ids: detail.homework.asset_ids }) })
    return () => { live = false }
    // `key` captures every field of `detail` the page list depends on.
  }, [key, workspace.client, workspace.studentId])
  return pages && pages.key === key ? pages.ids : null
}

export function PhotoPanel({ workspace, row, draft, detail, onReplacePage, focusVersion = 0 }: { workspace: Workspace; focusVersion?: number; row?: CaseDetail; draft: DraftView | null; detail: HomeworkDetail | null; onReplacePage?: (pageId: string) => void }) {
  const hasLocalPages = !!draft && !draft.correction && draft.pages.length > 0
  const serverPages = useHomeworkPages(workspace, hasLocalPages ? null : detail)
  const { sources, pageCount } = pageSources(draft, serverPages, row)
  const [currentKey, setCurrentKey] = useState<string | null>(null)
  const focus = row?.attempt.asset_ids[0]
  // Opening a question shows its answer photo, including when the same card
  // is selected again after manually browsing to another page.
  useEffect(() => { setCurrentKey(focus ?? null) }, [focus, row?.case.id, focusVersion])
  const found = sources.findIndex(s => s.key === currentKey || !!currentKey && s.assetId === currentKey), index = found >= 0 ? found : 0, current = sources[index]
  const [blob, setBlob] = useState<Blob | null>(null)
  const url = useObjectUrl(workspace.client, blob)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [expanded, setExpanded] = useState(false)
  const [size, setSize] = useState({ width: 0, height: 0 })
  const assetId = current?.assetId, localBlob = current?.blob ?? null
  useEffect(() => {
    let live = true
    setBlob(null); setSize({ width: 0, height: 0 }); setError('')
    if (localBlob || !assetId) { setBlob(localBlob); setLoading(false); return }
    setLoading(true)
    void workspace.client.assetBlob(assetId).then(value => { if (live) setBlob(value) }).catch(() => { if (live) setError('这一页照片暂时没能打开，请刷新后再看。') }).finally(() => { if (live) setLoading(false) })
    return () => { live = false }
  }, [assetId, localBlob, workspace.client, workspace.studentId])
  const select = (next: number) => { const target = sources[next]; if (target) setCurrentKey(target.key) }
  useEffect(() => {
    if (!expanded) return
    const keys = (event: KeyboardEvent) => { if (event.key === 'Escape') setExpanded(false); else if (event.key === 'ArrowLeft') select(index - 1); else if (event.key === 'ArrowRight') select(index + 1) }
    window.addEventListener('keydown', keys); return () => window.removeEventListener('keydown', keys)
  })
  // A region spanning the whole photo only says which page the question is on.
  const regions = row && assetId ? [...row.question.regions, ...row.attempt.answer_regions].filter(region => region.asset_id === assetId && !(region.x === 0 && region.y === 0 && region.width >= size.width && region.height >= size.height)) : []
  const waiting = !hasLocalPages && !!detail && serverPages === null && !sources.length
  const picture = url ? <div className="photo-image-wrap"><img src={url} alt={`${current?.label ?? '作业照片'}${assetId && row?.attempt.asset_ids.includes(assetId) ? '，这次作答对应的照片' : ''}`} onLoad={event => setSize({ width: event.currentTarget.naturalWidth, height: event.currentTarget.naturalHeight })} />{size.width > 0 && regions.length > 0 && <svg className="photo-regions" viewBox={`0 0 ${size.width} ${size.height}`} aria-hidden="true">{regions.map((region, i) => <rect key={i} x={region.x} y={region.y} width={region.width} height={region.height} rx="4" />)}</svg>}</div> : null
  const local = current?.localId != null
  const canReplace = local && !current?.uploaded && !!onReplacePage && draft?.stage === 'failed' && !workspace.busy
  return <section className="photo-panel" aria-label="作业原图">
    <div className="panel-heading"><span><span className="eyebrow">YOUR WORK</span><h2>{row ? '本次作答照片' : pageCount > 1 ? `这份作业共 ${pageCount} 页` : '先看清这张照片'}</h2></span>{local && <span className={`status-pill ${current?.uploaded ? 'green' : 'amber'}`}>{current?.uploaded ? '服务器已收到' : '还在这台设备'}</span>}<button className="icon-button" disabled={!url} aria-label="放大作业照片" onClick={() => setExpanded(true)}><Expand size={19} /></button></div>
    <PageNav sources={sources} pageCount={pageCount} index={index} onSelect={select} />
    <div className="photo-mat">{loading || waiting ? <div className="photo-placeholder"><LoaderCircle className="spin" size={24} /><p>正在打开原图…</p></div> : error ? <div className="photo-placeholder"><ImageOff size={30} /><p>{error}</p></div> : picture ?? <div className="photo-placeholder"><ImageOff size={30} /><p>这份记录的照片尚未加载</p></div>}</div>
    {canReplace && <div className="page-recover"><p className="muted small">这一页还没有上传成功。如果提示照片读取不到，可以重新选择这一页，其余页不用重拍。</p><button type="button" className="button secondary" onClick={() => onReplacePage?.(current!.localId!)}><RefreshCw size={15} /> 重新选择这一页</button></div>}
    <p className="photo-caption">{regions.length ? '绿色框为 AI 推测区域，请结合原图核对。' : pageCount > 1 ? `共 ${pageCount} 页，可以逐页翻看。检查结果只包括拍到并读出的题目。` : '题目和自己的作答都拍清楚，检查才更有依据。'}</p>
    {expanded && <div className="image-dialog" role="dialog" aria-modal="true" aria-label="放大作业照片" onKeyDown={event => { if (event.key === 'Tab') { event.preventDefault(); event.currentTarget.querySelector<HTMLButtonElement>('button')?.focus() } }} onClick={() => setExpanded(false)}><button className="icon-button" autoFocus aria-label="关闭放大照片" onClick={() => setExpanded(false)}><X /></button><div className="expanded-image" onClick={event => event.stopPropagation()}><PageNav sources={sources} pageCount={pageCount} index={index} onSelect={select} />{picture}</div></div>}
  </section>
}
