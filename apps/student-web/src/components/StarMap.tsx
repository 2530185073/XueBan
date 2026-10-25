import { useEffect, useId, useMemo, useRef, useState } from 'react'
import { Maximize2, Minus, Plus, Search, X } from 'lucide-react'
import type { KnowledgeGraphEdge, KnowledgeGraphNode } from '../core/learning'
import { clampZoom, layoutStarMap, neighbourhood, persistentLabels, starLabel, STAR_HEIGHT, STAR_WIDTH } from '../core/starMap'
import { sourceLabel, statusLabels } from '../learning'

type Camera = { x: number; y: number; k: number }
type Point = { x: number; y: number }
const HOME: Camera = { x: 0, y: 0, k: 1 }
const distance = (a: Point, b: Point) => Math.hypot(a.x - b.x, a.y - b.y)

/** Interactive personal atlas: semantic chapters, a real camera, and evidence-first nodes. */
export function StarMap({ nodes, edges, pending, selectedId, onOpen, demo = false }: {
  nodes: KnowledgeGraphNode[]; edges: KnowledgeGraphEdge[]; pending: boolean; selectedId?: string | null; onOpen: (id: string) => void
  demo?: boolean
}) {
  const uid = useId().replace(/:/g, '')
  const viewport = useRef<HTMLDivElement>(null)
  const buttons = useRef(new Map<string, SVGGElement>())
  const pointers = useRef(new Map<number, Point>())
  const drag = useRef<{ id: number; start: Point; camera: Camera } | null>(null)
  const pinch = useRef<{ distance: number; centre: Point; camera: Camera } | null>(null)
  const suppressClick = useRef(false)
  const [camera, setCamera] = useState<Camera>(HOME)
  const [cameraMotion, setCameraMotion] = useState(true)
  const [dragging, setDragging] = useState(false)
  const [hoveredId, setHoveredId] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const map = useMemo(() => layoutStarMap(nodes, edges), [nodes, edges])
  const activeId = hoveredId ?? selectedId ?? null
  const near = useMemo(() => neighbourhood(map, activeId), [map, activeId])
  const named = useMemo(() => persistentLabels(map, near.nodes, pending, camera.k > 1.45 ? 62 : 74, map.clusters.length === 1 || camera.k > 1.7), [map, near, pending, camera.k])
  const active = map.nodes.find(point => point.node.id === activeId)?.node ?? null
  const results = useMemo(() => {
    const term = query.trim().toLocaleLowerCase()
    return term ? map.nodes.filter(({ node }) => `${node.name}${node.chapter_name}`.toLocaleLowerCase().includes(term)).slice(0, 6) : []
  }, [map, query])

  useEffect(() => { setCamera(HOME); setCameraMotion(true); setHoveredId(null); setQuery('') }, [map])
  useEffect(() => {
    if (!selectedId) return
    const point = map.nodes.find(item => item.node.id === selectedId)
    if (!point) return
    setCameraMotion(true)
    setCamera(current => {
      const k = Math.max(current.k, 1.65)
      return { x: STAR_WIDTH / 2 - point.x * k, y: STAR_HEIGHT / 2 - point.y * k, k }
    })
  }, [selectedId, map])

  const toPoint = (clientX: number, clientY: number): Point => {
    const rect = viewport.current!.getBoundingClientRect()
    const scale = Math.min(rect.width / STAR_WIDTH, rect.height / STAR_HEIGHT)
    const offsetX = (rect.width - STAR_WIDTH * scale) / 2, offsetY = (rect.height - STAR_HEIGHT * scale) / 2
    return { x: (clientX - rect.left - offsetX) / scale, y: (clientY - rect.top - offsetY) / scale }
  }
  const zoomAt = (next: number, focal: Point = { x: STAR_WIDTH / 2, y: STAR_HEIGHT / 2 }, animate = false) => {
    setCameraMotion(animate)
    setCamera(current => {
      const k = clampZoom(next)
      const worldX = (focal.x - current.x) / current.k, worldY = (focal.y - current.y) / current.k
      return { x: focal.x - worldX * k, y: focal.y - worldY * k, k }
    })
  }
  const open = (id: string) => { if (!suppressClick.current) onOpen(id) }
  const chooseSearch = (id: string) => { setQuery(''); onOpen(id) }

  return <div className={`star-canvas ${dragging ? 'is-dragging' : ''}`} aria-label="可拖动和缩放的知识星图">
    <div className="star-search">
      <Search size={15} aria-hidden="true" /><input type="search" name="knowledge-search" autoComplete="off" value={query} onChange={event => setQuery(event.target.value)} placeholder="搜索知识点…" aria-label="搜索知识点" />
      {query && <button onClick={() => setQuery('')} aria-label="清空搜索"><X size={14} /></button>}
      {query && <div className="star-search-results">{results.length ? results.map(({ node }) => <button key={node.id} onClick={() => chooseSearch(node.id)}><span>{node.name}</span><small>{node.chapter_name}</small></button>) : <p>没有找到这个知识点</p>}</div>}
    </div>
    <div className="star-view-caption" aria-hidden="true"><strong>{map.clusters.length === 1 ? map.clusters[0].name : '知识地图'}</strong><small>{map.clusters.length} 个{demo ? '学科' : '章节'} · {map.nodes.length} 个知识点{demo ? '（模拟）' : ''}</small></div>
    <div className="star-camera-controls" aria-label="星图缩放控制">
      <button onClick={() => zoomAt(camera.k * 1.3, undefined, true)} disabled={camera.k >= 3.19} aria-label="放大星图"><Plus size={17} /></button>
      <span aria-live="polite">{camera.k.toFixed(1)}<small>×</small></span>
      <button onClick={() => zoomAt(camera.k / 1.3, undefined, true)} disabled={camera.k <= 0.73} aria-label="缩小星图"><Minus size={17} /></button>
      <button onClick={() => { setCameraMotion(true); setCamera(HOME) }} aria-label="还原全图"><Maximize2 size={15} /></button>
    </div>
    <div ref={viewport} className="star-viewport"
      onWheel={event => { event.preventDefault(); const point = toPoint(event.clientX, event.clientY); zoomAt(camera.k * Math.exp(-event.deltaY * 0.0012), point) }}
      onPointerDown={event => {
        if (event.button !== 0) return
        const point = { x: event.clientX, y: event.clientY }
        pointers.current.set(event.pointerId, point)
        suppressClick.current = false
        if (pointers.current.size === 1) drag.current = { id: event.pointerId, start: point, camera }
        if (pointers.current.size === 2) {
          const [a, b] = [...pointers.current.values()]
          pinch.current = { distance: distance(a, b), centre: toPoint((a.x + b.x) / 2, (a.y + b.y) / 2), camera }
          drag.current = null
          suppressClick.current = true
          for (const id of pointers.current.keys()) viewport.current?.setPointerCapture(id)
        }
        setDragging(true); setCameraMotion(false)
      }}
      onPointerMove={event => {
        if (!pointers.current.has(event.pointerId)) return
        pointers.current.set(event.pointerId, { x: event.clientX, y: event.clientY })
        if (!suppressClick.current && drag.current) {
          const start = drag.current.start
          if (Math.hypot(event.clientX - start.x, event.clientY - start.y) > 6) {
            suppressClick.current = true
            viewport.current?.setPointerCapture(event.pointerId)
          }
        }
        if (!suppressClick.current) return
        if (pinch.current && pointers.current.size >= 2) {
          const [a, b] = [...pointers.current.values()]
          const centre = toPoint((a.x + b.x) / 2, (a.y + b.y) / 2), start = pinch.current
          const k = clampZoom(start.camera.k * distance(a, b) / Math.max(1, start.distance))
          const worldX = (start.centre.x - start.camera.x) / start.camera.k, worldY = (start.centre.y - start.camera.y) / start.camera.k
          setCamera({ x: centre.x - worldX * k, y: centre.y - worldY * k, k })
        } else if (drag.current?.id === event.pointerId) {
          const start = drag.current, from = toPoint(start.start.x, start.start.y), to = toPoint(event.clientX, event.clientY)
          setCamera({ ...start.camera, x: start.camera.x + to.x - from.x, y: start.camera.y + to.y - from.y })
        }
      }}
      onPointerUp={event => {
        pointers.current.delete(event.pointerId); pinch.current = null
        if (viewport.current?.hasPointerCapture(event.pointerId)) viewport.current.releasePointerCapture(event.pointerId)
        const remaining = [...pointers.current.entries()][0]
        drag.current = remaining ? { id: remaining[0], start: remaining[1], camera } : null
        if (!remaining) { setDragging(false); window.setTimeout(() => { suppressClick.current = false }, 0) }
      }}
      onPointerCancel={event => { pointers.current.delete(event.pointerId); drag.current = null; pinch.current = null; suppressClick.current = false; setDragging(false) }}
      onDoubleClick={event => { const point = toPoint(event.clientX, event.clientY); zoomAt(camera.k * 1.45, point, true) }}>
      <svg className="star-map" viewBox={`0 0 ${map.width} ${map.height}`} role="group" aria-label={`${nodes.length} 个知识点，按 Tab 浏览并按回车查看证据`}>
        <defs>
          <radialGradient id={`${uid}-nebula-blue`}><stop offset="0" stopColor="#1f5f83" stopOpacity=".24" /><stop offset="1" stopColor="#1f5f83" stopOpacity="0" /></radialGradient>
          <radialGradient id={`${uid}-nebula-gold`}><stop offset="0" stopColor="#8b6942" stopOpacity=".13" /><stop offset="1" stopColor="#8b6942" stopOpacity="0" /></radialGradient>
          <linearGradient id={`${uid}-link`} x1="0" x2="1"><stop offset="0" stopColor="#8ddcc6" stopOpacity=".35" /><stop offset=".52" stopColor="#d7f5e9" stopOpacity=".95" /><stop offset="1" stopColor="#8fb7d8" stopOpacity=".35" /></linearGradient>
          <filter id={`${uid}-soft-glow`} x="-2" y="-2" width="4" height="4"><feGaussianBlur stdDeviation="3.2" result="blur" /><feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge></filter>
          <marker id={`${uid}-arrow`} viewBox="0 0 8 8" refX="7" refY="4" markerWidth="5" markerHeight="5" orient="auto"><path d="M 1 1 L 7 4 L 1 7" /></marker>
        </defs>
        <rect width={STAR_WIDTH} height={STAR_HEIGHT} fill={`url(#${uid}-nebula-blue)`} />
        <ellipse cx="930" cy="650" rx="490" ry="340" fill={`url(#${uid}-nebula-gold)`} />
        <g className="star-ambient" aria-hidden="true">{map.ambient.map((star, index) => <circle key={index} cx={star.x} cy={star.y} r={star.r} opacity={star.opacity} />)}</g>
        <g className={`star-world ${cameraMotion && !dragging ? 'is-animated' : ''}`} transform={`translate(${camera.x} ${camera.y}) scale(${camera.k})`}>
          {map.clusters.length > 1 && <g className="star-centre" aria-hidden="true">
            <circle cx={STAR_WIDTH / 2} cy={STAR_HEIGHT / 2} r="72" /><circle cx={STAR_WIDTH / 2} cy={STAR_HEIGHT / 2} r="57" />
            <text x={STAR_WIDTH / 2} y={STAR_HEIGHT / 2 + 14} textAnchor="middle" className="star-centre-title">知识地图</text>
            <text x={STAR_WIDTH / 2} y={STAR_HEIGHT / 2 + 37} textAnchor="middle" className="star-centre-meta">{map.nodes.length} 个{demo ? '模拟' : '真实'}知识节点</text>
          </g>}
          <g className="star-clusters" aria-hidden="true">{map.clusters.map(cluster => <g key={cluster.id} className={`star-cluster c${cluster.index % 6}`}>
            <circle className="star-cluster-atmosphere" cx={cluster.x} cy={cluster.y} r={cluster.radius * 1.55} />
            <circle className="star-cluster-orbit outer" cx={cluster.x} cy={cluster.y} r={cluster.radius * 1.22} />
            <circle className="star-cluster-orbit inner" cx={cluster.x} cy={cluster.y} r={cluster.radius * 1.08} />
            <text className="star-cluster-index" x={cluster.x} y={cluster.y - cluster.radius * 1.22 - 29} textAnchor="middle">{demo ? '初一 · 示例' : `第 ${cluster.index + 1} 章`}</text>
            <text className="star-cluster-name" x={cluster.x} y={cluster.y - cluster.radius * 1.22 - 9} textAnchor="middle">{cluster.name}</text>
            {!pending && <text className="star-cluster-tally" x={cluster.x} y={cluster.y - cluster.radius * 1.22 + 9} textAnchor="middle">{cluster.count} 个知识点</text>}
          </g>)}</g>
          <g className="star-links" aria-hidden="true">{map.links.map(link => {
            const lit = near.links.has(link.id), quiet = activeId && !lit
            return <path key={link.id} d={link.path} className={`${link.cross ? 'cross' : ''} ${lit ? 'lit' : ''} ${quiet ? 'dim' : ''}`} markerEnd={lit ? `url(#${uid}-arrow)` : undefined} />
          })}</g>
          {map.nodes.map((point, index) => {
            const { node, x, y, r } = point, status = statusLabels[node.learning_status], state = pending ? 'pending' : node.learning_status
            const related = !activeId || near.nodes.has(node.id), cluster = map.clusters[point.chapterIndex]
            const dx = x - cluster.x, dy = y - cluster.y, horizontal = Math.abs(dx) > Math.abs(dy) * 0.72
            const labelX = horizontal ? (dx >= 0 ? r + 10 : -r - 10) : 0, labelY = horizontal ? 4 : (dy >= 0 ? r + 17 : -r - 13)
            return <g key={node.id} ref={element => { if (element) buttons.current.set(node.id, element); else buttons.current.delete(node.id) }}
              transform={`translate(${x} ${y})`} role="button" tabIndex={0} aria-pressed={selectedId === node.id}
              aria-label={`${node.name}，${pending ? '正在更新' : `${status.label}，${demo ? '模拟状态' : sourceLabel(node.assessment_source)}`}，查看${demo ? '模拟作答' : '作答证据'}`}
              className={`star-node ${state} ${selectedId === node.id ? 'selected' : ''} ${related ? '' : 'dim'} ${named.has(node.id) ? 'named' : ''}`}
              onClick={() => open(node.id)} onMouseEnter={() => setHoveredId(node.id)} onMouseLeave={() => setHoveredId(null)}
              onFocus={() => setHoveredId(node.id)} onBlur={() => setHoveredId(null)} onKeyDown={event => {
                if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onOpen(node.id) }
                const offset = event.key === 'ArrowRight' || event.key === 'ArrowDown' ? 1 : event.key === 'ArrowLeft' || event.key === 'ArrowUp' ? -1 : 0
                if (offset) { event.preventDefault(); const next = map.nodes[(index + offset + map.nodes.length) % map.nodes.length]; buttons.current.get(next.node.id)?.focus() }
              }}>
              <title>{node.name}</title><circle className="star-hit" r={Math.max(18, r + 10)} />
              <circle className="star-halo" r={r * 2.8} />
              <circle className="star-selected-ring outer" r={r + 9} /><circle className="star-selected-ring" r={r + 5} />
              <circle className="star-core" r={r} filter={activeId === node.id ? `url(#${uid}-soft-glow)` : undefined} />
              {state === 'stable_for_now' && <circle className="star-spark" r={Math.max(1.4, r * 0.28)} />}
              {state === 'needs_help_now' && <circle className="star-attention" r={r + 3.5} />}
              <text className="star-label" x={labelX} y={labelY} textAnchor={horizontal ? (dx >= 0 ? 'start' : 'end') : 'middle'}>{starLabel(node.name)}</text>
            </g>
          })}
        </g>
      </svg>
    </div>
    {active && <div className={`star-node-inspector ${pending ? 'pending' : active.learning_status}`} aria-live="polite">
      <span>{active.chapter_name}</span><strong>{active.name}</strong>
      <small>{pending ? '正在整理最新记录' : `${statusLabels[active.learning_status].label} · ${demo ? '模拟状态' : sourceLabel(active.assessment_source)}`}</small>
      <em>{pending ? '更新完成后再看状态' : active.valid_evidence_count ? `${active.valid_evidence_count} 条${demo ? '模拟作答 · 点击查看示例' : '有效作答 · 点击查看证据'}` : '还没有判断 · 点击查看说明'}</em>
    </div>}
    <div className="star-gesture-hint" aria-hidden="true"><span className="mouse-glyph" />拖动探索 · 滚轮或双指缩放 · 点击看{demo ? '示例' : '证据'}</div>
  </div>
}
