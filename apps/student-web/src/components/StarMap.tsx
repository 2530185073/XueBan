import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react'
import {
  ChevronRight,
  Maximize2,
  Minimize2,
  RotateCcw,
  ShieldCheck,
  Sparkles,
  ZoomIn,
  ZoomOut,
} from 'lucide-react'
import type { KnowledgeGraphEdge, KnowledgeGraphNode, LearningStatus } from '../core/learning'
import {
  layoutStarMap,
  neighbourhood,
  persistentLabels,
  starLabel,
  STAR_HEIGHT,
  STAR_WIDTH,
  type DemoStoryStep,
  type StarNode,
} from '../core/starMap'
import { sourceLabel, statusLabels } from '../learning'

export interface StarMapProps {
  nodes: KnowledgeGraphNode[]
  edges: KnowledgeGraphEdge[]
  pending: boolean
  selectedId?: string | null
  onOpen: (id: string) => void
  filterStatus?: LearningStatus | 'all'
  activeChapterId?: string | null
  onSelectChapter?: (chapterId: string | 'all') => void
  tourStep?: DemoStoryStep | null
}

const CHAPTER_NEBULA_COLORS = [
  { stop1: '#38bdf8', stop2: '#1e40af' }, // Ch 1: Azure Sky
  { stop1: '#818cf8', stop2: '#3730a3' }, // Ch 2: Indigo Electric
  { stop1: '#c084fc', stop2: '#581c87' }, // Ch 3: Cosmic Purple
  { stop1: '#34d399', stop2: '#065f46' }, // Ch 4: Emerald Aurora
  { stop1: '#fbbf24', stop2: '#92400e' }, // Ch 5: Solar Amber
  { stop1: '#60a5fa', stop2: '#1e3a8a' }, // Ch 6: Royal Deep
]

export function StarMap({
  nodes,
  edges,
  pending,
  selectedId,
  onOpen,
  filterStatus = 'all',
  activeChapterId,
  onSelectChapter,
  tourStep,
}: StarMapProps) {
  const uid = useId().replace(/:/g, '')
  const map = useMemo(() => layoutStarMap(nodes, edges), [nodes, edges])
  const near = useMemo(() => neighbourhood(map, selectedId), [map, selectedId])
  const named = useMemo(() => persistentLabels(map, near.nodes, pending), [map, near, pending])
  const buttons = useRef(new Map<string, SVGGElement>())

  // Viewport camera state (pan & zoom)
  const [scale, setScale] = useState(1)
  const [pan, setPan] = useState({ x: 0, y: 0 })
  const [isDragging, setIsDragging] = useState(false)
  const [isFlying, setIsFlying] = useState(false)
  const [isFullscreen, setIsFullscreen] = useState(false)
  const [hoveredNode, setHoveredNode] = useState<StarNode | null>(null)
  const [hoverPos, setHoverPos] = useState({ x: 0, y: 0 })
  const [mouseParallax, setMouseParallax] = useState({ x: 0, y: 0 })

  const containerRef = useRef<HTMLDivElement>(null)
  const pointerStartRef = useRef({ x: 0, y: 0, panX: 0, panY: 0 })
  const animTimeoutRef = useRef<number | null>(null)

  const focusing = !!selectedId && near.nodes.size > 0

  // 60fps Smooth Camera Fly-to Helper
  const flyTo = useCallback((targetX: number, targetY: number, targetScale = 1.35) => {
    if (!containerRef.current) return
    const rect = containerRef.current.getBoundingClientRect()
    const containerW = rect.width || STAR_WIDTH
    const containerH = rect.height || STAR_HEIGHT

    const nextScale = Math.max(0.7, Math.min(2.4, targetScale))
    const nextPanX = containerW / 2 - targetX * nextScale
    const nextPanY = containerH / 2 - targetY * nextScale

    setIsFlying(true)
    setScale(nextScale)
    setPan({ x: nextPanX, y: nextPanY })

    if (animTimeoutRef.current) window.clearTimeout(animTimeoutRef.current)
    animTimeoutRef.current = window.setTimeout(() => {
      setIsFlying(false)
    }, 450)
  }, [])

  const resetView = useCallback(() => {
    setIsFlying(true)
    if (containerRef.current) {
      const rect = containerRef.current.getBoundingClientRect()
      const w = rect.width || map.width
      const h = rect.height || map.height
      const fitScale = Math.min(w / map.width, h / map.height)
      const clampedScale = Math.max(0.65, Math.min(1.2, Math.round(fitScale * 100) / 100))
      const nextPanX = Math.round((w - map.width * clampedScale) / 2)
      const nextPanY = Math.round((h - map.height * clampedScale) / 2)
      setScale(clampedScale)
      setPan({ x: nextPanX, y: nextPanY })
    } else {
      setScale(1)
      setPan({ x: 0, y: 0 })
    }
    if (animTimeoutRef.current) window.clearTimeout(animTimeoutRef.current)
    animTimeoutRef.current = window.setTimeout(() => setIsFlying(false), 450)
  }, [map.width, map.height])

  const zoomIn = useCallback(() => {
    setIsFlying(true)
    setScale(prev => Math.min(2.6, Math.round((prev + 0.25) * 100) / 100))
    if (animTimeoutRef.current) window.clearTimeout(animTimeoutRef.current)
    animTimeoutRef.current = window.setTimeout(() => setIsFlying(false), 250)
  }, [])

  const zoomOut = useCallback(() => {
    setIsFlying(true)
    setScale(prev => Math.max(0.65, Math.round((prev - 0.25) * 100) / 100))
    if (animTimeoutRef.current) window.clearTimeout(animTimeoutRef.current)
    animTimeoutRef.current = window.setTimeout(() => setIsFlying(false), 250)
  }, [])

  // Auto-fly to selected node, tour step focal node, or active chapter cluster
  useEffect(() => {
    const focalId = tourStep?.focalNodeId || selectedId
    if (focalId) {
      const target = map.nodes.find(n => n.node.id === focalId)
      if (target) {
        flyTo(target.x, target.y, 1.45)
        return
      }
    }
    if (activeChapterId && activeChapterId !== 'all') {
      const cluster = map.clusters.find(c => c.id === activeChapterId)
      if (cluster) {
        flyTo(cluster.x, cluster.y, 1.42)
        return
      }
    }
    if (!selectedId && (!activeChapterId || activeChapterId === 'all') && !tourStep) {
      resetView()
    }
  }, [selectedId, tourStep, activeChapterId, map.nodes, map.clusters, flyTo, resetView])

  // Mouse wheel zoom centered on cursor (industry standard)
  const handleWheel = (e: React.WheelEvent) => {
    e.preventDefault()
    if (!containerRef.current) return
    const rect = containerRef.current.getBoundingClientRect()
    const mouseX = e.clientX - rect.left
    const mouseY = e.clientY - rect.top

    const zoomFactor = e.deltaY < 0 ? 1.14 : 0.88
    const nextScale = Math.max(0.65, Math.min(2.8, scale * zoomFactor))

    // Zoom centered on mouse point
    const nextPanX = mouseX - (mouseX - pan.x) * (nextScale / scale)
    const nextPanY = mouseY - (mouseY - pan.y) * (nextScale / scale)

    setIsFlying(false)
    setScale(nextScale)
    setPan({ x: nextPanX, y: nextPanY })
  }

  // Pointer drag panning (works on mouse, touch, and stylus)
  const handlePointerDown = (e: React.PointerEvent) => {
    if ((e.target as HTMLElement).closest('.star-node') || (e.target as HTMLElement).closest('button')) return
    setIsDragging(true)
    setIsFlying(false)
    pointerStartRef.current = {
      x: e.clientX,
      y: e.clientY,
      panX: pan.x,
      panY: pan.y,
    }
    ;(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId)
  }

  const handlePointerMove = (e: React.PointerEvent) => {
    if (isDragging) {
      const dx = e.clientX - pointerStartRef.current.x
      const dy = e.clientY - pointerStartRef.current.y
      setPan({
        x: pointerStartRef.current.panX + dx,
        y: pointerStartRef.current.panY + dy,
      })
    } else if (containerRef.current) {
      // Subtle 3D parallax on ambient dust
      const rect = containerRef.current.getBoundingClientRect()
      const nx = (e.clientX - rect.left) / rect.width - 0.5
      const ny = (e.clientY - rect.top) / rect.height - 0.5
      setMouseParallax({ x: nx * 14, y: ny * 14 })
    }
  }

  const handlePointerUp = (e: React.PointerEvent) => {
    if (isDragging) {
      setIsDragging(false)
      try {
        ;(e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId)
      } catch {
        /* ignore */
      }
    }
  }

  // Hover peek card positioning
  const handleStarMouseEnter = (star: StarNode, e: React.MouseEvent) => {
    if (containerRef.current) {
      const rect = containerRef.current.getBoundingClientRect()
      const rawX = e.clientX - rect.left
      const rawY = e.clientY - rect.top
      setHoverPos({
        x: Math.min(rect.width - 270, Math.max(16, rawX + 18)),
        y: Math.min(rect.height - 200, Math.max(16, rawY - 30)),
      })
    }
    setHoveredNode(star)
  }

  const handleStarMouseLeave = () => {
    setHoveredNode(null)
  }

  return (
    <div
      ref={containerRef}
      className={`star-canvas ${isFullscreen ? 'fullscreen-mode' : ''} ${isDragging ? 'dragging' : ''}`}
      aria-label="可滑动与缩放查看的沉浸式知识星图"
      onWheel={handleWheel}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={handlePointerUp}
      onPointerCancel={handlePointerUp}
    >
      {/* Dynamic 3D Parallax Space Dust Layer */}
      <div
        className="star-backdrop-glow"
        aria-hidden="true"
        style={{
          transform: `translate3d(${mouseParallax.x * 0.5}px, ${mouseParallax.y * 0.5}px, 0)`,
          transition: 'transform 0.2s cubic-bezier(0.1, 1, 0.2, 1)',
        }}
      />
      <div
        className="star-stardust-layer"
        aria-hidden="true"
        style={{
          transform: `translate3d(${mouseParallax.x}px, ${mouseParallax.y}px, 0)`,
          transition: 'transform 0.15s cubic-bezier(0.1, 1, 0.2, 1)',
        }}
      />

      {/* Floating Glass HUD Controls */}
      <div className="star-hud-toolbar" role="toolbar" aria-label="星图视角与缩放控制">
        <div className="hud-pill-group">
          <button
            type="button"
            className="hud-btn"
            onClick={zoomIn}
            aria-label="放大星图"
            title="放大视角 (+)"
          >
            <ZoomIn size={16} />
          </button>
          <span className="hud-zoom-level">{scale.toFixed(1)}x</span>
          <button
            type="button"
            className="hud-btn"
            onClick={zoomOut}
            aria-label="缩小星图"
            title="缩小视角 (-)"
          >
            <ZoomOut size={16} />
          </button>
          <div className="hud-divider" />
          <button
            type="button"
            className="hud-btn"
            onClick={resetView}
            aria-label="复位全景"
            title="复位至全景星系"
          >
            <RotateCcw size={15} />
          </button>
          <button
            type="button"
            className={`hud-btn ${isFullscreen ? 'active' : ''}`}
            onClick={() => setIsFullscreen(!isFullscreen)}
            aria-label={isFullscreen ? '退出全屏' : '全屏观测'}
            title={isFullscreen ? '退出全屏观测' : '全屏沉浸观测模式'}
          >
            {isFullscreen ? <Minimize2 size={16} /> : <Maximize2 size={16} />}
          </button>
        </div>

        {filterStatus !== 'all' && (
          <div className="hud-filter-badge" role="status">
            <Sparkles size={14} />
            <span>
              已聚焦：
              {filterStatus === 'needs_help_now'
                ? '重点关注'
                : filterStatus === 'consolidating'
                  ? '正在巩固'
                  : filterStatus === 'stable_for_now'
                    ? '阶段较稳'
                    : '未判断'}
            </span>
          </div>
        )}
      </div>

      {/* Hardware-Accelerated Viewport Wrapper */}
      <div
        className="star-viewport-layer"
        style={{
          transform: `translate3d(${pan.x}px, ${pan.y}px, 0) scale(${scale})`,
          transformOrigin: '0 0',
          transition: isFlying ? 'transform 0.45s cubic-bezier(0.16, 1, 0.3, 1)' : 'none',
          willChange: 'transform',
        }}
      >
        <svg
          className="star-map"
          viewBox={`0 0 ${map.width} ${map.height}`}
          width={map.width}
          height={map.height}
          role="group"
          aria-label={`${nodes.length} 个知识点，按 Tab 浏览并按回车查看证据`}
        >
          <defs>
            {/* Gradients for Nebulae */}
            {CHAPTER_NEBULA_COLORS.map((col, idx) => (
              <radialGradient key={idx} id={`${uid}-nebula-${idx}`}>
                <stop offset="0" stopColor={col.stop1} stopOpacity=".34" />
                <stop offset=".45" stopColor={col.stop2} stopOpacity=".15" />
                <stop offset="1" stopColor={col.stop2} stopOpacity="0" />
              </radialGradient>
            ))}

            {/* High-Performance Native Halo Gradients */}
            <radialGradient id={`${uid}-halo-stable`}>
              <stop offset="0" stopColor="#34d399" stopOpacity=".95" />
              <stop offset=".35" stopColor="#10b981" stopOpacity=".45" />
              <stop offset="1" stopColor="#059669" stopOpacity="0" />
            </radialGradient>
            <radialGradient id={`${uid}-halo-consolidating`}>
              <stop offset="0" stopColor="#60a5fa" stopOpacity=".9" />
              <stop offset=".4" stopColor="#3b82f6" stopOpacity=".4" />
              <stop offset="1" stopColor="#1d4ed8" stopOpacity="0" />
            </radialGradient>
            <radialGradient id={`${uid}-halo-needs`}>
              <stop offset="0" stopColor="#fbbf24" stopOpacity="1" />
              <stop offset=".3" stopColor="#f59e0b" stopOpacity=".7" />
              <stop offset=".65" stopColor="#ef4444" stopOpacity=".22" />
              <stop offset="1" stopColor="#b91c1c" stopOpacity="0" />
            </radialGradient>

            {/* Directed Starlight Energy Flows */}
            <linearGradient id={`${uid}-flow-upstream`} x1="0" y1="0" x2="1" y2="0">
              <stop offset="0" stopColor="#34d399" stopOpacity=".1" />
              <stop offset=".5" stopColor="#6ee7b7" stopOpacity=".95" />
              <stop offset="1" stopColor="#10b981" stopOpacity=".2" />
            </linearGradient>
            <linearGradient id={`${uid}-flow-downstream`} x1="0" y1="0" x2="1" y2="0">
              <stop offset="0" stopColor="#fbbf24" stopOpacity=".2" />
              <stop offset=".5" stopColor="#fef08a" stopOpacity="1" />
              <stop offset="1" stopColor="#f59e0b" stopOpacity=".1" />
            </linearGradient>
          </defs>

          {/* Astrolabe Precision Celestial Coordinate Grid */}
          <g className="star-astrolabe-grid" aria-hidden="true">
            <circle cx={STAR_WIDTH / 2} cy={STAR_HEIGHT / 2} r={170} className="astrolabe-ring" />
            <circle cx={STAR_WIDTH / 2} cy={STAR_HEIGHT / 2} r={340} className="astrolabe-ring" />
            <circle cx={STAR_WIDTH / 2} cy={STAR_HEIGHT / 2} r={510} className="astrolabe-ring" />
            <line x1={STAR_WIDTH / 2} y1="30" x2={STAR_WIDTH / 2} y2={STAR_HEIGHT - 30} className="astrolabe-axis" />
            <line x1="30" y1={STAR_HEIGHT / 2} x2={STAR_WIDTH - 30} y2={STAR_HEIGHT / 2} className="astrolabe-axis" />
          </g>

          {/* Chapter Constellation Nebulae & Headers */}
          <g className="star-clusters" aria-hidden="true">
            {map.clusters.map(cluster => {
              const isChapterActive = !activeChapterId || activeChapterId === 'all' || activeChapterId === cluster.id
              const colorIdx = cluster.index % CHAPTER_NEBULA_COLORS.length
              return (
                <g
                  key={cluster.id}
                  className={`star-cluster c${colorIdx} ${isChapterActive ? '' : 'cluster-dim'}`}
                  onClick={() => onSelectChapter?.(cluster.id)}
                  style={{ cursor: onSelectChapter ? 'pointer' : 'default' }}
                >
                  {/* Luminous Nebula Cloud */}
                  <circle
                    cx={cluster.x}
                    cy={cluster.y}
                    r={cluster.radius * 2.3}
                    fill={`url(#${uid}-nebula-${colorIdx})`}
                  />
                  {/* Outer Orbit Constellation Ring */}
                  <circle
                    className="star-cluster-ring"
                    cx={cluster.x}
                    cy={cluster.y}
                    r={cluster.radius * 1.35}
                  />
                  <circle
                    className="star-cluster-subring"
                    cx={cluster.x}
                    cy={cluster.y}
                    r={cluster.radius * 0.9}
                  />

                  {/* Chapter Title Badge Pill */}
                  <g className="cluster-header-badge" transform={`translate(${cluster.x}, ${cluster.y - cluster.radius * 1.35 - 18})`}>
                    <rect
                      x="-78"
                      y="-15"
                      width="156"
                      height="30"
                      rx="15"
                      className="cluster-badge-pill"
                    />
                    <text className="star-cluster-index" x="-48" y="5" textAnchor="middle">
                      {String(cluster.index + 1).padStart(2, '0')}
                    </text>
                    <text className="star-cluster-name" x="12" y="5" textAnchor="middle">
                      {cluster.name}
                    </text>
                  </g>

                  {/* Status Tally Numbers */}
                  {!pending && (
                    <text
                      className="star-cluster-tally"
                      x={cluster.x}
                      y={cluster.y - cluster.radius * 1.35 + 24}
                      textAnchor="middle"
                    >
                      {(['stable_for_now', 'consolidating', 'needs_help_now'] as const)
                        .filter(key => cluster.counts[key])
                        .map(key => (
                          <tspan key={key} className={key} dx="8">
                            ● {cluster.counts[key]}
                          </tspan>
                        ))}
                    </text>
                  )}
                </g>
              )
            })}
          </g>

          {/* Constellation Prerequisite Energy Links */}
          <g className="star-links" aria-hidden="true">
            {map.links.map(link => {
              const isUpstream = near.upstreamLinks?.has(link.id)
              const isDownstream = near.downstreamLinks?.has(link.id)
              const isLit = isUpstream || isDownstream || near.links.has(link.id)
              const isDimmed = focusing && !isLit

              return (
                <path
                  key={link.id}
                  d={link.path}
                  className={`star-link-path ${link.cross ? 'cross' : ''} ${isUpstream ? 'upstream-lit' : ''} ${isDownstream ? 'downstream-lit' : ''} ${isLit ? 'lit' : ''} ${isDimmed ? 'dim' : ''}`}
                />
              )
            })}
          </g>

          {/* Knowledge Node Stars */}
          <g className="star-nodes-layer">
            {map.nodes.map(({ node, x, y, r }, index) => {
              const status = statusLabels[node.learning_status]
              const state = pending ? 'pending' : node.learning_status
              const isSelected = selectedId === node.id
              const isNeighbour = near.nodes.has(node.id)
              const isUpstream = near.upstreamNodes?.has(node.id)
              const isDownstream = near.downstreamNodes?.has(node.id)

              const matchesFilter = filterStatus === 'all' || node.learning_status === filterStatus
              const matchesChapter = !activeChapterId || activeChapterId === 'all' || node.chapter_id === activeChapterId

              // Focused dimming logic
              const isDimmed = !matchesFilter || !matchesChapter || (focusing && !isNeighbour)
              const haloType =
                state === 'stable_for_now'
                  ? 'stable'
                  : state === 'consolidating'
                    ? 'consolidating'
                    : state === 'needs_help_now'
                      ? 'needs'
                      : null

              const isKeyNamed =
                named.has(node.id) ||
                isSelected ||
                hoveredNode?.node.id === node.id ||
                (filterStatus === 'needs_help_now' && node.learning_status === 'needs_help_now') ||
                (activeChapterId && activeChapterId !== 'all' && matchesChapter)

              return (
                <g
                  key={node.id}
                  ref={element => {
                    if (element) buttons.current.set(node.id, element)
                    else buttons.current.delete(node.id)
                  }}
                  transform={`translate(${x} ${y})`}
                  role="button"
                  tabIndex={0}
                  aria-pressed={isSelected}
                  aria-label={`${node.name}，${pending ? '正在更新' : `${status.label}，${sourceLabel(node.assessment_source)}`}，查看作答证据`}
                  className={`star-node ${state} ${isSelected ? 'selected' : ''} ${isDimmed ? 'dim' : ''} ${isKeyNamed ? 'named' : ''} ${isUpstream ? 'node-upstream' : ''} ${isDownstream ? 'node-downstream' : ''}`}
                  onClick={() => onOpen(node.id)}
                  onMouseEnter={e => handleStarMouseEnter({ node, x, y, r, chapterIndex: 0 }, e)}
                  onMouseLeave={handleStarMouseLeave}
                  onKeyDown={event => {
                    if (event.key === 'Enter' || event.key === ' ') {
                      event.preventDefault()
                      onOpen(node.id)
                    }
                    const offset =
                      event.key === 'ArrowRight' || event.key === 'ArrowDown'
                        ? 1
                        : event.key === 'ArrowLeft' || event.key === 'ArrowUp'
                          ? -1
                          : 0
                    if (offset) {
                      event.preventDefault()
                      const next = map.nodes[(index + offset + map.nodes.length) % map.nodes.length]
                      buttons.current.get(next.node.id)?.focus()
                    }
                  }}
                >
                  <title>{node.name}</title>

                  {/* Generous Clickable Hit Target */}
                  <circle className="star-hit" r={Math.max(28, r + 14)} />

                  {/* Breathing Radial Halo Corona */}
                  {haloType && !isDimmed && (
                    <circle
                      className="star-halo"
                      r={r * 3.6}
                      fill={`url(#${uid}-halo-${haloType})`}
                      style={{ animationDelay: `${(index % 8) * 0.4}s` }}
                    />
                  )}

                  {/* Pulsating Attention Wave for Needs Help */}
                  {state === 'needs_help_now' && !isDimmed && (
                    <circle className="star-beacon-pulse" r={r * 2.2} />
                  )}

                  {/* Selection Orbit Ring */}
                  <circle className="star-selected-ring" r={r + 8} />

                  {/* Core Starlight Sphere */}
                  <circle className="star-core" r={r} />

                  {/* Diffraction Diamond Cross Flare for Mastered Nodes */}
                  {state === 'stable_for_now' && !isDimmed && (
                    <g className="star-sparkle-cross" aria-hidden="true">
                      <line x1={-r * 1.5} y1="0" x2={r * 1.5} y2="0" stroke="#d1fae5" strokeWidth="1.2" />
                      <line x1="0" y1={-r * 1.5} x2="0" y2={r * 1.5} stroke="#d1fae5" strokeWidth="1.2" />
                      <circle className="star-spark" r={r * 0.4} fill="#ffffff" />
                    </g>
                  )}

                  {/* Crystal-Clear Pill Label */}
                  <g className="star-label-group" transform={`translate(0, ${r + 17})`}>
                    <rect
                      x="-44"
                      y="-11"
                      width="88"
                      height="20"
                      rx="10"
                      className="star-label-pill-bg"
                    />
                    <text className="star-label" textAnchor="middle" y="3">
                      {starLabel(node.name)}
                    </text>
                  </g>
                </g>
              )
            })}
          </g>
        </svg>
      </div>

      {/* Floating Hover Peek Tooltip Card */}
      {hoveredNode && !isDragging && (
        <div
          className="star-peek-card"
          style={{
            left: `${hoverPos.x}px`,
            top: `${hoverPos.y}px`,
          }}
          role="tooltip"
        >
          <div className="peek-header">
            <span className="peek-chapter">
              {hoveredNode.node.chapter_name || '知识点'}
            </span>
            <span className={`status-pill ${statusLabels[hoveredNode.node.learning_status].tone}`}>
              {statusLabels[hoveredNode.node.learning_status].label}
            </span>
          </div>
          <h4 className="peek-title">{hoveredNode.node.name}</h4>
          <div className="peek-meta">
            <span className="peek-source">
              <ShieldCheck size={13} />
              {sourceLabel(hoveredNode.node.assessment_source)}
            </span>
            <span className="peek-evidence">
              {hoveredNode.node.valid_evidence_count} 次作答记录
            </span>
          </div>
          {hoveredNode.node.observable_objective && (
            <p className="peek-desc">
              {hoveredNode.node.observable_objective}
            </p>
          )}
          <div className="peek-action">
            <span>点击查看作业原卷与订正</span>
            <ChevronRight size={14} />
          </div>
        </div>
      )}

      {/* Micro-Galaxy Minimap Radar (Clickable!) */}
      <div
        className="star-minimap"
        aria-hidden="true"
        onClick={() => resetView()}
        title="点击全景复位"
      >
        <svg viewBox={`0 0 ${STAR_WIDTH} ${STAR_HEIGHT}`}>
          {map.clusters.map(cluster => (
            <circle
              key={cluster.id}
              cx={cluster.x}
              cy={cluster.y}
              r={cluster.radius}
              className="minimap-cluster"
            />
          ))}
          {map.nodes.map(n => (
            <circle
              key={n.node.id}
              cx={n.x}
              cy={n.y}
              r={4}
              className={`minimap-dot ${n.node.learning_status}`}
            />
          ))}
          {/* Current Camera Viewport Frame */}
          <rect
            x={Math.max(0, -pan.x / scale)}
            y={Math.max(0, -pan.y / scale)}
            width={STAR_WIDTH / scale}
            height={STAR_HEIGHT / scale}
            className="minimap-viewport"
          />
        </svg>
      </div>
    </div>
  )
}
