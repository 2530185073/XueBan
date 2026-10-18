import { useEffect, useRef, useState, type PointerEvent } from 'react'
import { ArrowLeft, ArrowRight, FileImage, Focus, LoaderCircle, Maximize2, PenLine, RefreshCw, X, ZoomIn } from 'lucide-react'
import type { ImageAsset, Region } from '../api/contracts.generated'
import { aspectMatches, regionFromDrag, toOriginal, type Point } from '../geometry'

export interface DraftLayer { label: string; regions: Region[]; active: boolean }
export interface PhotoLayers { question: Region[]; answer: Region[]; others: { label: string; regions: Region[] }[]; drafts: DraftLayer[] }
export interface DrawingTool { allowedAssets: string[] | null; onDraw: (region: Region) => void; hint: string }

const zooms = [1, 1.5, 2]

export default function PhotoViewer({ assets, labels, assetId, onAsset, loadImage, layers, drawing, expanded, onExpand }: {
  assets: ImageAsset[]; labels: Map<string, string>; assetId: string | null; onAsset: (id: string) => void
  loadImage: (asset: ImageAsset, signal: AbortSignal) => Promise<string>
  layers: PhotoLayers; drawing: DrawingTool | null; expanded: boolean; onExpand: (value: boolean) => void
}) {
  const index = Math.max(0, assets.findIndex(asset => asset.id === assetId))
  const asset = assets[index] as ImageAsset | undefined
  const [url, setUrl] = useState<string | null>(null)
  const [state, setState] = useState<'loading' | 'ready' | 'failed'>('loading')
  const [attempt, setAttempt] = useState(0)
  const [natural, setNatural] = useState({ width: 0, height: 0 })
  const [showQuestion, setShowQuestion] = useState(true)
  const [showAnswer, setShowAnswer] = useState(true)
  const [zoom, setZoom] = useState(1)
  const [drag, setDrag] = useState<{ start: Point; end: Point } | null>(null)
  const svg = useRef<SVGSVGElement>(null)
  // The drag origin lives in a ref: pointerup can arrive before React commits the pointerdown state.
  const origin = useRef<Point | null>(null)

  // Keyed on identity, not the object: a workspace reload must not blank a photo that is already shown.
  const assetKey = asset ? `${asset.id}:${asset.revision}` : null
  useEffect(() => {
    if (!asset) return
    const controller = new AbortController()
    setUrl(null); setState('loading'); setNatural({ width: 0, height: 0 }); setDrag(null); origin.current = null
    loadImage(asset, controller.signal).then(value => { if (!controller.signal.aborted) setUrl(value) }).catch(() => { if (!controller.signal.aborted) setState('failed') })
    return () => controller.abort()
  }, [assetKey, loadImage, attempt])

  if (!asset) return <div className="photo-error"><FileImage size={34} /><p>这份作业没有可读取的原图</p><small>原图可能尚未上传完成。不会用其他图片替代。</small></div>

  const size = { width: asset.width, height: asset.height }
  const aligned = state === 'ready' && aspectMatches(natural, size)
  const drawable = !!drawing && aligned && (!drawing.allowedAssets || drawing.allowedAssets.includes(asset.id))
  const on = (regions: Region[]) => regions.filter(region => region.asset_id === asset.id)
  // A recognized region spanning the whole photo only says which page the question is on.
  const located = (regions: Region[]) => on(regions).filter(region => !(region.x === 0 && region.y === 0 && region.width >= size.width && region.height >= size.height))
  const stroke = Math.max(2, asset.width / 300)
  const font = Math.max(14, asset.width / 38)
  const point = (event: PointerEvent<SVGSVGElement>) => toOriginal(event.clientX, event.clientY, svg.current!.getBoundingClientRect(), size)

  function down(event: PointerEvent<SVGSVGElement>) {
    if (!drawable || event.button !== 0) return
    event.currentTarget.setPointerCapture(event.pointerId)
    const start = point(event); origin.current = start; setDrag({ start, end: start })
  }
  function move(event: PointerEvent<SVGSVGElement>) { if (origin.current) setDrag({ start: origin.current, end: point(event) }) }
  function up(event: PointerEvent<SVGSVGElement>) {
    const start = origin.current
    origin.current = null; setDrag(null)
    if (!start || !drawing) return
    const region = regionFromDrag(asset!.id, start, point(event), size)
    if (region) drawing.onDraw(region)
  }
  const preview = drag ? regionFromDrag(asset.id, drag.start, drag.end, size) : null
  const label = (region: Region, text: string, className: string) => <text className={className} x={region.x + stroke * 2} y={region.y + font} fontSize={font}>{text}</text>

  return <>
    <div className="image-toolbar">
      <div><span className="view-label"><FileImage size={15} />{labels.get(asset.id) ?? '作业原图'}</span><span className="page-indicator">第 {asset.page_index + 1} 页 · {index + 1} / {assets.length}</span></div>
      <div>
        <button className={`icon-button ${showQuestion ? 'is-on' : ''}`} title="显示题区" aria-label="显示或隐藏题区" aria-pressed={showQuestion} onClick={() => setShowQuestion(!showQuestion)}><Focus size={17} /></button>
        <button className={`icon-button ${showAnswer ? 'is-on' : ''}`} title="显示答案区" aria-label="显示或隐藏答案区" aria-pressed={showAnswer} onClick={() => setShowAnswer(!showAnswer)}><PenLine size={16} /></button>
        <button className="icon-button" title="放大" aria-label={`放大原图，当前 ${zoom} 倍`} onClick={() => setZoom(zooms[(zooms.indexOf(zoom) + 1) % zooms.length])}><ZoomIn size={16} /><span className="zoom-value">{zoom}×</span></button>
        <button className="icon-button" title={expanded ? '收起' : '展开原图'} aria-label={expanded ? '收起原图' : '展开原图'} onClick={() => onExpand(!expanded)}>{expanded ? <X size={17} /> : <Maximize2 size={16} />}</button>
      </div>
    </div>
    {drawing && <div className={`draw-hint ${drawable ? '' : 'blocked'}`}>{!aligned && state === 'ready' ? '显示的原图尺寸与记录不一致（可能存在方向旋转），不能在此图上框选。' : drawable ? drawing.hint : state !== 'ready' ? '原图加载后即可框选。' : '这张图不属于当前可框选的范围，请切换到对应照片。'}</div>}
    <div className="paper-stage"><div className="paper-wrap" style={zoom > 1 ? { width: `${zoom * 100}%`, maxWidth: 'none' } : undefined}>
      {url && state !== 'failed' && <img key={url} src={url} alt="作业原图" referrerPolicy="no-referrer" draggable={false}
        onLoad={event => { setNatural({ width: event.currentTarget.naturalWidth, height: event.currentTarget.naturalHeight }); setState('ready') }} onError={() => setState('failed')} />}
      {state === 'ready' && <svg ref={svg} className={`region-overlay ${drawable ? 'drawing' : ''}`} viewBox={`0 0 ${asset.width} ${asset.height}`} preserveAspectRatio="none" role="img" aria-label={drawable ? '原图框选区域：按住拖动画框' : '题区与答案区'}
        onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={() => { origin.current = null; setDrag(null) }}>
        {layers.others.flatMap((other, i) => located(other.regions).map((region, j) => <g key={`o${i}-${j}`}><rect className="other-region" x={region.x} y={region.y} width={region.width} height={region.height} strokeWidth={stroke} />{label(region, other.label, 'region-label muted')}</g>))}
        {showQuestion && located(layers.question).map((region, i) => <g key={`q${i}`}><rect className="question-region" x={region.x} y={region.y} width={region.width} height={region.height} strokeWidth={stroke} />{label(region, '题区', 'region-label')}</g>)}
        {showAnswer && located(layers.answer).map((region, i) => <g key={`a${i}`}><rect className="answer-region" x={region.x} y={region.y} width={region.width} height={region.height} strokeWidth={stroke} />{label(region, '答案', 'region-label answer')}</g>)}
        {layers.drafts.flatMap((draft, i) => on(draft.regions).map((region, j) => <g key={`d${i}-${j}`}><rect className={`draft-region ${draft.active ? 'active' : ''}`} x={region.x} y={region.y} width={region.width} height={region.height} strokeWidth={stroke * 1.4} />{label(region, draft.label, 'region-label draft')}</g>))}
        {preview && <rect className="draft-region active preview" x={preview.x} y={preview.y} width={preview.width} height={preview.height} strokeWidth={stroke * 1.4} />}
      </svg>}
      {state === 'loading' && <div className="photo-loading"><LoaderCircle className="spin" size={22} /><span>正在读取原图…</span></div>}
      {state === 'failed' && <div className="photo-error"><FileImage size={34} /><p>原图暂时不可读取</p><small>读取凭证可能已过期或网络中断。不会用其他图片替代。</small><button className="button" onClick={() => setAttempt(attempt + 1)}><RefreshCw size={15} />重新读取</button></div>}
    </div></div>
    <div className="document-footer">
      <span><span className="region-legend" />题区<span className="region-legend answer" />答案区{layers.drafts.length > 0 && <><span className="region-legend draft" />新框选</>} · 原图像素 {asset.width}×{asset.height}</span>
      {assets.length > 1 && <div className="button-row">
        <button className="icon-button" disabled={index === 0} aria-label="上一张原图" onClick={() => onAsset(assets[index - 1].id)}><ArrowLeft size={16} /></button>
        <button className="icon-button" disabled={index === assets.length - 1} aria-label="下一张原图" onClick={() => onAsset(assets[index + 1].id)}><ArrowRight size={16} /></button>
      </div>}
    </div>
  </>
}
