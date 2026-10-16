import { useEffect, useState } from 'react'
import { Expand, ImageOff, LoaderCircle, X } from 'lucide-react'
import type { CaseDetail, DraftView, HomeworkDetail } from '../core/types'
import type { Workspace } from '../useStudentWorkspace'
import { useObjectUrl } from './useObjectUrl'

export function PhotoPanel({ workspace, row, draft, detail }: { workspace: Workspace; row?: CaseDetail; draft: DraftView | null; detail: HomeworkDetail | null }) {
  const [blob, setBlob] = useState<Blob | null>(null)
  const url = useObjectUrl(workspace.client, blob)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [expanded, setExpanded] = useState(false)
  const [size, setSize] = useState({ width: 0, height: 0 })
  const assetId = row?.attempt.asset_ids[0] ?? detail?.homework.asset_ids[0]
  useEffect(() => {
    let live = true
    setBlob(null); setSize({ width: 0, height: 0 }); setError('')
    if (!assetId) { setBlob(draft?.photo ?? null); setLoading(false); return }
    setLoading(true)
    void workspace.client.assetBlob(assetId).then(value => { if (live) setBlob(value) }).catch(() => { if (live) setError('这次作答的照片暂时没能打开，请刷新后再看。') }).finally(() => { if (live) setLoading(false) })
    return () => { live = false }
  }, [assetId, draft?.id, workspace.client, workspace.studentId])
  useEffect(() => { if (!expanded) return; const close = (event: KeyboardEvent) => { if (event.key === 'Escape') setExpanded(false) }; window.addEventListener('keydown', close); return () => window.removeEventListener('keydown', close) }, [expanded])
  const regions = row ? [...row.question.regions, ...row.attempt.answer_regions].filter(region => region.asset_id === assetId) : []
  const picture = url ? <div className="photo-image-wrap"><img src={url} alt={row ? '这次作答对应的作业照片' : '准备检查的作业照片'} onLoad={event => setSize({ width: event.currentTarget.naturalWidth, height: event.currentTarget.naturalHeight })} />{size.width > 0 && regions.length > 0 && <svg className="photo-regions" viewBox={`0 0 ${size.width} ${size.height}`} aria-hidden="true">{regions.map((region, index) => <rect key={index} x={region.x} y={region.y} width={region.width} height={region.height} rx="4" />)}</svg>}</div> : null
  return <section className="photo-panel" aria-label="作业原图">
    <div className="panel-heading"><span><span className="eyebrow">YOUR WORK</span><h2>{row ? '本次作答照片' : '先看清这张照片'}</h2></span><button className="icon-button" disabled={!url} aria-label="放大作业照片" onClick={() => setExpanded(true)}><Expand size={19} /></button></div>
    <div className="photo-mat">{loading ? <div className="photo-placeholder"><LoaderCircle className="spin" size={24} /><p>正在打开原图…</p></div> : error ? <div className="photo-placeholder"><ImageOff size={30} /><p>{error}</p></div> : picture ?? <div className="photo-placeholder"><ImageOff size={30} /><p>这份记录的照片尚未加载</p></div>}</div>
    <p className="photo-caption">{regions.length ? '绿色框为 AI 推测区域，请结合原图核对。' : '题目和自己的作答都拍清楚，检查才更有依据。'}</p>
    {expanded && <div className="image-dialog" role="dialog" aria-modal="true" aria-label="放大作业照片" onKeyDown={event => { if (event.key === 'Tab') { event.preventDefault(); event.currentTarget.querySelector<HTMLButtonElement>('button')?.focus() } }} onClick={() => setExpanded(false)}><button className="icon-button" autoFocus aria-label="关闭放大照片" onClick={() => setExpanded(false)}><X /></button><div className="expanded-image" onClick={event => event.stopPropagation()}>{picture}</div></div>}
  </section>
}
