import { useEffect, useId, useState } from 'react'
import { ArrowLeft, ArrowRight, Camera, Check, Crop as CropIcon, ImagePlus, RefreshCw, RotateCcw, RotateCw, Trash2, Undo2, X } from 'lucide-react'
import { LIMITS_TEXT, MAX_BATCH_BYTES, MAX_PAGES, mib, pageLabel, type Crop } from '../core'
import type { DraftPageView, DraftView } from '../core/types'
import type { Workspace } from '../useStudentWorkspace'
import { useObjectUrl } from './useObjectUrl'

const SIDES = [['top', '上边'], ['bottom', '下边'], ['left', '左边'], ['right', '右边']] as const
type Margins = Record<(typeof SIDES)[number][0], number>
const NO_MARGINS: Margins = { top: 0, bottom: 0, left: 0, right: 0 }
/** Percent cut from each side of the shown page, as a crop of that page. */
export function marginsToCrop(m: Margins): Crop | null {
  if (!m.top && !m.bottom && !m.left && !m.right) return null
  return { x: m.left / 100, y: m.top / 100, width: 1 - (m.left + m.right) / 100, height: 1 - (m.top + m.bottom) / 100 }
}

function Thumb({ workspace, page, index, selected, onSelect }: { workspace: Workspace; page: DraftPageView; index: number; selected: boolean; onSelect: () => void }) {
  const url = useObjectUrl(workspace.client, page.blob)
  return <li><button type="button" className={`page-thumb ${selected ? 'selected' : ''}`} aria-pressed={selected} aria-label={`查看第 ${index + 1} 页`} onClick={onSelect}>{url && <img src={url} alt="" />}<span>{index + 1}</span></button></li>
}

/** Pages of a draft that has not been sent: preview, order, turn, crop, retake, delete, add. */
export function DraftPages({ workspace, draft, onAdd, onReplace }: { workspace: Workspace; draft: DraftView; onAdd: (camera: boolean) => void; onReplace: (pageId: string) => void }) {
  const [selected, setSelected] = useState({ id: draft.pages[0]?.id ?? '', index: 0 })
  const [cropping, setCropping] = useState(false)
  const [margins, setMargins] = useState<Margins>(NO_MARGINS)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const id = useId(), busy = !!workspace.busy, pages = draft.pages
  const found = pages.findIndex(p => p.id === selected.id), index = found >= 0 ? found : Math.max(0, Math.min(selected.index, pages.length - 1))
  const page = pages[index], url = useObjectUrl(workspace.client, page?.blob ?? null)
  const total = pages.reduce((sum, p) => sum + p.size, 0), single = !!draft.correction
  const select = (next: number) => { const target = pages[next]; if (target) setSelected({ id: target.id, index: next }); setCropping(false); setConfirmDelete(false) }
  useEffect(() => { setCropping(false); setMargins(NO_MARGINS); setConfirmDelete(false) }, [page?.id, page?.blob])
  if (!page) return null
  const crop = marginsToCrop(margins)
  const run = (task: Promise<boolean>) => { void task.then(ok => { if (ok) { setCropping(false); setMargins(NO_MARGINS) } }) }
  return <section className="photo-panel draft-pages" aria-label="准备上传的作业照片">
    <div className="panel-heading"><span><span className="eyebrow">CHECK EACH PAGE</span><h2>{single ? '看清这张订正照片' : pages.length > 1 ? `检查这 ${pages.length} 页` : '先看清这张照片'}</h2></span><span className="status-pill amber">只在这台设备</span></div>
    <div className="photo-mat">{url ? <div className="photo-image-wrap"><img src={url} alt={`准备上传的${single ? '订正照片' : `第 ${index + 1} 页`}`} />{cropping && <svg className="crop-overlay" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true"><path fillRule="evenodd" d={`M0 0H100V100H0Z M${margins.left} ${margins.top}H${100 - margins.right}V${100 - margins.bottom}H${margins.left}Z`} /><rect x={margins.left} y={margins.top} width={100 - margins.left - margins.right} height={100 - margins.top - margins.bottom} /></svg>}</div> : null}</div>
    {!single && <div className="page-status"><span aria-live="polite">{pageLabel(index, pages.length)}</span><span className="muted small">{page.width}×{page.height} · {mib(page.size)}{page.edited ? ' · 已调整' : ''}</span></div>}
    {cropping ? <fieldset className="crop-panel" disabled={busy}>
      <legend>裁掉多余的边（每边最多 45%）</legend>
      {SIDES.map(([side, label]) => <label key={side} htmlFor={`${id}-${side}`}><span>{label}</span><input id={`${id}-${side}`} type="range" min={0} max={45} step={1} value={margins[side]} onChange={event => setMargins(old => ({ ...old, [side]: Number(event.target.value) }))} /><output htmlFor={`${id}-${side}`}>{margins[side]}%</output></label>)}
      <div className="crop-actions"><button type="button" className="button quiet" onClick={() => { setCropping(false); setMargins(NO_MARGINS) }}><X size={16} /> 取消</button><button type="button" className="button primary" disabled={!crop} onClick={() => crop && run(workspace.editPage(page.id, { crop }))}><Check size={16} /> 应用裁切</button></div>
    </fieldset>
      : confirmDelete ? <div className="page-confirm" role="group" aria-label="确认删除这一页"><p>{pages.length > 1 ? `删除第 ${index + 1} 页？其他页会保留。` : '这是最后一页，删除后这份草稿也会一起移除。'}</p><button type="button" className="button quiet" disabled={busy} onClick={() => setConfirmDelete(false)}>先不删</button><button type="button" className="button danger" disabled={busy} onClick={() => run(workspace.removePage(page.id))}><Trash2 size={16} /> 确认删除</button></div>
      : <div className="page-tools" role="toolbar" aria-label="调整这一页">
        {!single && <><button type="button" className="icon-button" disabled={busy || index === 0} aria-label="往前移一页" title="往前移" onClick={() => run(workspace.movePage(page.id, -1))}><ArrowLeft size={18} /></button><button type="button" className="icon-button" disabled={busy || index === pages.length - 1} aria-label="往后移一页" title="往后移" onClick={() => run(workspace.movePage(page.id, 1))}><ArrowRight size={18} /></button><span className="tool-gap" /></>}
        <button type="button" className="icon-button" disabled={busy} aria-label="向左转 90 度" title="向左转" onClick={() => run(workspace.editPage(page.id, { rotate: -1 }))}><RotateCcw size={18} /></button>
        <button type="button" className="icon-button" disabled={busy} aria-label="向右转 90 度" title="向右转" onClick={() => run(workspace.editPage(page.id, { rotate: 1 }))}><RotateCw size={18} /></button>
        <button type="button" className="icon-button" disabled={busy} aria-label="裁切这一页" title="裁切" onClick={() => setCropping(true)}><CropIcon size={18} /></button>
        {page.edited && <button type="button" className="icon-button" disabled={busy} aria-label="恢复原图" title="恢复原图" onClick={() => run(workspace.editPage(page.id, { reset: true }))}><Undo2 size={18} /></button>}
        <span className="tool-gap" />
        <button type="button" className="text-button" disabled={busy} onClick={() => onReplace(page.id)}><RefreshCw size={15} /> 换一张</button>
        <button type="button" className="text-button danger-text" disabled={busy} onClick={() => setConfirmDelete(true)}><Trash2 size={15} /> 删除</button>
      </div>}
    {!single && <div className="page-strip-wrap">
      <ol className="page-strip" aria-label="页面顺序">{pages.map((p, i) => <Thumb key={p.id} workspace={workspace} page={p} index={i} selected={i === index} onSelect={() => select(i)} />)}
        {pages.length < MAX_PAGES && <li className="page-add"><button type="button" className="page-thumb add" disabled={busy} onClick={() => onAdd(false)} aria-label="从相册再加一页"><ImagePlus size={20} /><span>加一页</span></button><button type="button" className="text-button" disabled={busy} onClick={() => onAdd(true)}><Camera size={14} /> 拍一页</button></li>}
      </ol>
      <div className="page-budget"><meter min={0} max={MAX_BATCH_BYTES} value={total} aria-label="照片合计大小" /><span className="muted small">共 {pages.length} 页 · {mib(total)} / 8 MiB · {LIMITS_TEXT}</span></div>
      {pages.length >= MAX_PAGES && <p className="muted small page-note">已经 {MAX_PAGES} 页了。还有更多页，可以先检查这几页，再新建一份作业。</p>}
    </div>}
    <p className="photo-caption">题目和自己的作答都拍清楚，检查才更有依据。照片开始检查后才会上传。</p>
  </section>
}
