import { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { Crosshair, Minus, Plus } from 'lucide-react'
import type { KnowledgeGraphEdge, KnowledgeGraphNode } from '../core/learning'
import { constrain, fitBox, focusOn, labelTier, lerpCamera, MAX_ZOOM, MIN_ZOOM, panBy, pinch, sameCamera, zoomAt, type Camera } from '../core/camera'
import { layoutStarMap, neighbourhood, STAR_HEIGHT, STAR_WIDTH, starLabel } from '../core/starMap'
import { sourceLabel, statusLabels } from '../learning'

const WORLD = { x: 0, y: 0, width: STAR_WIDTH, height: STAR_HEIGHT }
const reduceMotion = () => typeof window !== 'undefined' && !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches

/**
 * The stage. One <g> carries the camera transform; pan, zoom and glides change that single attribute through
 * requestAnimationFrame and never touch React state per frame, so 75 stars stay smooth on a tablet.
 */
export function Galaxy({ nodes, edges, pending, selectedId, focusChapterId, onOpen, coveredRight = 0 }: {
  nodes: KnowledgeGraphNode[]; edges: KnowledgeGraphEdge[]; pending: boolean; selectedId?: string | null
  focusChapterId?: string | null; onOpen: (id: string) => void; coveredRight?: number
}) {
  const uid = useId().replace(/:/g, '')
  const map = useMemo(() => layoutStarMap(nodes, edges), [nodes, edges])
  const near = useMemo(() => neighbourhood(map, selectedId), [map, selectedId])
  const hostRef = useRef<HTMLDivElement>(null), worldRef = useRef<SVGGElement>(null), tierRef = useRef<HTMLDivElement>(null)
  const size = useRef({ width: 1000, height: 640 }), camera = useRef<Camera>({ x: 0, y: 0, k: 1 }), frame = useRef(0)
  const pointers = useRef(new Map<number, { x: number; y: number }>()), drag = useRef<{ moved: boolean; origin?: { x: number; y: number } } | null>(null)
  const [hover, setHover] = useState<string | null>(null), [zoom, setZoom] = useState(1), [tier, setTier] = useState<'clusters' | 'attention' | 'all'>('clusters')
  const byId = useMemo(() => new Map(map.nodes.map(n => [n.node.id, n])), [map])
  const hoverNear = useMemo(() => neighbourhood(map, hover), [map, hover])

  const apply = useCallback((next: Camera) => {
    camera.current = next
    worldRef.current?.setAttribute('transform', `translate(${next.x} ${next.y}) scale(${next.k})`)
    const t = labelTier(next.k)
    if (tierRef.current && tierRef.current.dataset.tier !== t) { tierRef.current.dataset.tier = t; setTier(t) }
    setZoom(Math.round(next.k * 10) / 10)
  }, [])
  const glide = useCallback((to: Camera, ms = 650) => {
    cancelAnimationFrame(frame.current)
    const from = camera.current
    if (reduceMotion() || sameCamera(from, to)) { apply(to); return }
    const start = performance.now()
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / ms)
      apply(lerpCamera(from, to, t))
      if (t < 1) frame.current = requestAnimationFrame(tick)
    }
    frame.current = requestAnimationFrame(tick)
  }, [apply])
  const fit = useCallback(() => fitBox(WORLD, size.current, 24), [])
  const bounded = useCallback((c: Camera) => constrain(c, WORLD, size.current, 140), [])

  // Track the stage size; refit when it changes (rotation, drawer open/close) without losing a deliberate zoom.
  useLayoutEffect(() => {
    const host = hostRef.current
    if (!host) return
    const measure = () => {
      const r = host.getBoundingClientRect()
      size.current = { width: Math.max(1, r.width), height: Math.max(1, r.height) }
    }
    measure()
    apply(fit())
    const observer = new ResizeObserver(() => { const wasFit = sameCamera(camera.current, fit(), 0.5); measure(); if (wasFit) apply(fit()) })
    observer.observe(host)
    return () => { observer.disconnect(); cancelAnimationFrame(frame.current) }
  }, [apply, fit, map])

  // Selecting a star flies to it, to the left of the drawer; closing the drawer flies back out.
  const lastSelected = useRef<string | null>(null)
  useEffect(() => {
    if (selectedId && byId.has(selectedId)) { const p = byId.get(selectedId)!; glide(bounded(focusOn(camera.current, p, size.current, coveredRight))); lastSelected.current = selectedId }
    else if (lastSelected.current && !selectedId) { glide(fit()); lastSelected.current = null }
  }, [selectedId, byId, coveredRight, glide, bounded, fit])
  useEffect(() => {
    if (!focusChapterId) { if (!selectedId) glide(fit()); return }
    const cluster = map.clusters.find(c => c.id === focusChapterId)
    if (cluster) glide(bounded(focusOn(camera.current, cluster, size.current, coveredRight, Math.min(2.2, 560 / (cluster.radius * 2.4)))))
  }, [focusChapterId]) // eslint-disable-line react-hooks/exhaustive-deps

  // Wheel needs a non-passive listener to stop the page from scrolling while the student zooms the map.
  useEffect(() => {
    const host = hostRef.current
    if (!host) return
    const onWheel = (event: WheelEvent) => {
      event.preventDefault()
      const rect = host.getBoundingClientRect()
      const factor = Math.exp(-event.deltaY * (event.ctrlKey ? 0.01 : 0.0016))
      cancelAnimationFrame(frame.current)
      apply(bounded(zoomAt(camera.current, { x: event.clientX - rect.left, y: event.clientY - rect.top }, factor)))
    }
    host.addEventListener('wheel', onWheel, { passive: false })
    return () => host.removeEventListener('wheel', onWheel)
  }, [apply, bounded])

  const local = (event: { clientX: number; clientY: number }) => { const r = hostRef.current!.getBoundingClientRect(); return { x: event.clientX - r.left, y: event.clientY - r.top } }
  const onPointerDown = (event: React.PointerEvent) => {
    if ((event.target as Element).closest('.galaxy-controls')) return
    cancelAnimationFrame(frame.current)
    // No capture yet: a tap must still reach the star as a normal click. Capture only once it really becomes a drag.
    pointers.current.set(event.pointerId, local(event)); drag.current = { moved: false }
  }
  const onPointerMove = (event: React.PointerEvent) => {
    const previous = pointers.current.get(event.pointerId)
    if (!previous) return
    const point = local(event)
    if (pointers.current.size === 2) {
      const before: [{ x: number; y: number }, { x: number; y: number }] = [...pointers.current.values()] as never
      pointers.current.set(event.pointerId, point)
      const after: [{ x: number; y: number }, { x: number; y: number }] = [...pointers.current.values()] as never
      const g = pinch(before, after)
      apply(bounded(panBy(zoomAt(camera.current, g.from, g.factor), g.to.x - g.from.x, g.to.y - g.from.y)))
      if (drag.current) drag.current.moved = true
      return
    }
    const dx = point.x - previous.x, dy = point.y - previous.y
    if (drag.current && !drag.current.moved) {
      const origin = drag.current.origin ?? previous
      drag.current.origin = origin
      if (Math.hypot(point.x - origin.x, point.y - origin.y) < 6) return
    }
    pointers.current.set(event.pointerId, point)
    // A tap with a little wobble is still a tap; only real movement becomes a drag.
    if (drag.current && !drag.current.moved) { drag.current.moved = true; try { hostRef.current?.setPointerCapture(event.pointerId) } catch { /* pointer already gone */ } }
    apply(bounded(panBy(camera.current, dx, dy)))
  }
  const onPointerUp = (event: React.PointerEvent) => { pointers.current.delete(event.pointerId); if (!pointers.current.size) setTimeout(() => { drag.current = null }, 0) }
  const step = (factor: number) => glide(bounded(zoomAt(camera.current, { x: size.current.width / 2, y: size.current.height / 2 }, factor)), 260)
  const onKey = (event: React.KeyboardEvent) => {
    if (event.target !== event.currentTarget) return
    const move = 70, keys: Record<string, () => void> = {
      ArrowLeft: () => apply(bounded(panBy(camera.current, move, 0))), ArrowRight: () => apply(bounded(panBy(camera.current, -move, 0))),
      ArrowUp: () => apply(bounded(panBy(camera.current, 0, move))), ArrowDown: () => apply(bounded(panBy(camera.current, 0, -move))),
      '+': () => step(1.3), '=': () => step(1.3), '-': () => step(1 / 1.3), '0': () => glide(fit()),
    }
    if (keys[event.key]) { event.preventDefault(); keys[event.key]() }
  }

  const lit = (id: string) => (selectedId ? near.nodes.has(id) : hover ? hoverNear.nodes.has(id) : true)
  const litLink = (id: string) => (selectedId ? near.links.has(id) : hover ? hoverNear.links.has(id) : false)
  const dimming = !!selectedId || !!hover
  const showName = (id: string, status: string, r: number) => {
    if (id === selectedId || id === hover || near.nodes.has(id) || hoverNear.nodes.has(id)) return true
    if (tier === 'all') return true
    return tier === 'attention' && !pending && status === 'needs_help_now' && r > 0
  }

  return <div className="galaxy" ref={hostRef} data-tier={tier} onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp} onPointerCancel={onPointerUp}
    onKeyDown={onKey} tabIndex={-1}>
    <div ref={tierRef} data-tier={tier} hidden />
    <div className="galaxy-sky" aria-hidden="true"><i className="g1" /><i className="g2" /><i className="g3" /></div>
    <svg className="galaxy-svg" role="group" aria-label={`${nodes.length} 个知识点的知识星系。方向键移动，加号减号缩放，Tab 浏览知识点，回车查看证据。`}>
      <defs>
        {(['stable', 'consolidating', 'needs'] as const).map(name => {
          const colour = name === 'stable' ? '#7cf5c0' : name === 'consolidating' ? '#8fb8ff' : '#ffb86b'
          return <radialGradient key={name} id={`${uid}-${name}`}><stop offset="0" stopColor={colour} stopOpacity=".95" /><stop offset=".45" stopColor={colour} stopOpacity=".28" /><stop offset="1" stopColor={colour} stopOpacity="0" /></radialGradient>
        })}
        <radialGradient id={`${uid}-neb`}><stop offset="0" stopColor="#6d8cff" stopOpacity=".34" /><stop offset="1" stopColor="#6d8cff" stopOpacity="0" /></radialGradient>
      </defs>
      <g ref={worldRef}>
        <g className="galaxy-clusters" aria-hidden="true">
          {map.clusters.map(c => <g key={c.id} className={`galaxy-cluster c${c.index % 6} ${focusChapterId === c.id ? 'on' : ''}`}>
            <circle cx={c.x} cy={c.y} r={c.radius * 2.1} fill={`url(#${uid}-neb)`} />
            <circle className="galaxy-ring" cx={c.x} cy={c.y} r={c.radius * 1.3} />
            <circle className="galaxy-ring inner" cx={c.x} cy={c.y} r={c.radius * 0.72} />
          </g>)}
        </g>
        <g className="galaxy-links" aria-hidden="true">
          {map.links.map(l => <path key={l.id} d={l.path} className={`${l.cross ? 'cross' : ''} ${litLink(l.id) ? 'lit' : ''} ${dimming && !litLink(l.id) ? 'dim' : ''}`} />)}
        </g>
        {map.nodes.map(({ node, x, y, r }, index) => {
          const status = statusLabels[node.learning_status], state = pending ? 'pending' : node.learning_status
          const halo = state === 'stable_for_now' ? 'stable' : state === 'consolidating' ? 'consolidating' : state === 'needs_help_now' ? 'needs' : null
          const on = lit(node.id)
          return <g key={node.id} transform={`translate(${x} ${y})`} role="button" tabIndex={0} aria-pressed={selectedId === node.id}
            aria-label={`${node.name}，${pending ? '正在更新' : `${status.label}，${sourceLabel(node.assessment_source)}`}，查看作答证据`}
            className={`galaxy-star ${state} ${selectedId === node.id ? 'selected' : ''} ${on ? '' : 'dim'} ${hover === node.id ? 'hover' : ''}`}
            onClick={() => { if (!drag.current?.moved) onOpen(node.id) }} onPointerEnter={() => setHover(node.id)} onPointerLeave={() => setHover(h => (h === node.id ? null : h))}
            onFocus={() => setHover(node.id)} onBlur={() => setHover(h => (h === node.id ? null : h))}
            onKeyDown={event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onOpen(node.id) } }}>
            <title>{node.name}</title>
            <circle className="galaxy-hit" r={Math.max(26, r + 16)} />
            {halo && <circle className="galaxy-halo" r={r * 3.6} fill={`url(#${uid}-${halo})`} style={{ animationDelay: `${(index % 9) * 0.37}s` }} />}
            <circle className="galaxy-orbit" r={r + 8} />
            <circle className="galaxy-core" r={r} />
            {state === 'stable_for_now' && <circle className="galaxy-spark" r={r * 0.36} />}
            {showName(node.id, node.learning_status, r) && <text className="galaxy-name" y={r + 17} textAnchor="middle">{starLabel(node.name, tier === 'all' || selectedId === node.id || hover === node.id ? 12 : 8)}</text>}
          </g>
        })}
        <g className="galaxy-titles" aria-hidden="true">
          {map.clusters.map(c => <g key={c.id} className={`galaxy-title ${tier === 'all' ? 'quiet' : ''}`}>
            <text className="idx" x={c.x} y={c.y - c.radius * 1.3 - 30} textAnchor="middle">{String(c.index + 1).padStart(2, '0')}</text>
            <text className="name" x={c.x} y={c.y - c.radius * 1.3 - 8} textAnchor="middle">{c.name}</text>
            {!pending && <text className="tally" x={c.x} y={c.y - c.radius * 1.3 + 12} textAnchor="middle">
              {(['stable_for_now', 'consolidating', 'needs_help_now'] as const).filter(key => c.counts[key]).map(key => <tspan key={key} className={key} dx="9">● {c.counts[key]}</tspan>)}
            </text>}
          </g>)}
        </g>
      </g>
    </svg>
    <div className="galaxy-controls" role="group" aria-label="地图缩放">
      <button type="button" aria-label="放大" onClick={() => step(1.3)} disabled={zoom >= Math.round(MAX_ZOOM * 10) / 10}><Plus size={20} /></button>
      <span className="galaxy-zoom" aria-live="off">{zoom.toFixed(1)}×</span>
      <button type="button" aria-label="缩小" onClick={() => step(1 / 1.3)} disabled={zoom <= Math.round(MIN_ZOOM * 10) / 10}><Minus size={20} /></button>
      <button type="button" aria-label="回到全景" onClick={() => glide(fit())}><Crosshair size={20} /></button>
    </div>
  </div>
}
