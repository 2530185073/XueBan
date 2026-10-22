import { useId, useMemo, useRef } from 'react'
import type { KnowledgeGraphEdge, KnowledgeGraphNode } from '../core/learning'
import { layoutStarMap, neighbourhood, persistentLabels, starLabel } from '../core/starMap'
import { sourceLabel, statusLabels } from '../learning'

/** Constellation view: chapters are star clusters, a lit star is a point with a valid answer behind it. */
export function StarMap({ nodes, edges, pending, selectedId, onOpen }: {
  nodes: KnowledgeGraphNode[]; edges: KnowledgeGraphEdge[]; pending: boolean; selectedId?: string | null; onOpen: (id: string) => void
}) {
  const uid = useId().replace(/:/g, '')
  const map = useMemo(() => layoutStarMap(nodes, edges), [nodes, edges])
  const near = useMemo(() => neighbourhood(map, selectedId), [map, selectedId])
  const named = useMemo(() => persistentLabels(map, near.nodes, pending), [map, near, pending])
  const buttons = useRef(new Map<string, SVGGElement>())
  const focusing = !!selectedId && near.nodes.size > 0
  return <div className="star-canvas" aria-label="可滑动查看的知识星图">
    <svg className="star-map" viewBox={`0 0 ${map.width} ${map.height}`} role="group" aria-label={`${nodes.length} 个知识点，按 Tab 浏览并按回车查看证据`}>
      <defs>
        <radialGradient id={`${uid}-nebula`}><stop offset="0" stopColor="#5b8cff" stopOpacity=".28" /><stop offset="1" stopColor="#5b8cff" stopOpacity="0" /></radialGradient>
        <radialGradient id={`${uid}-halo-stable`}><stop offset="0" stopColor="#7cf5c0" stopOpacity=".85" /><stop offset="1" stopColor="#7cf5c0" stopOpacity="0" /></radialGradient>
        <radialGradient id={`${uid}-halo-consolidating`}><stop offset="0" stopColor="#8fb8ff" stopOpacity=".7" /><stop offset="1" stopColor="#8fb8ff" stopOpacity="0" /></radialGradient>
        <radialGradient id={`${uid}-halo-needs`}><stop offset="0" stopColor="#ffb86b" stopOpacity=".85" /><stop offset="1" stopColor="#ffb86b" stopOpacity="0" /></radialGradient>
        <linearGradient id={`${uid}-flow`} x1="0" x2="1"><stop offset="0" stopColor="#7cf5c0" stopOpacity=".05" /><stop offset=".5" stopColor="#9fd0ff" stopOpacity=".7" /><stop offset="1" stopColor="#7cf5c0" stopOpacity=".05" /></linearGradient>
      </defs>
      <g className="star-clusters" aria-hidden="true">
        {map.clusters.map(cluster => <g key={cluster.id} className={`star-cluster c${cluster.index % 6}`}>
          <circle cx={cluster.x} cy={cluster.y} r={cluster.radius * 1.9} fill={`url(#${uid}-nebula)`} />
          <circle className="star-cluster-ring" cx={cluster.x} cy={cluster.y} r={cluster.radius * 1.28} />
          <text className="star-cluster-index" x={cluster.x} y={cluster.y - cluster.radius * 1.28 - 22} textAnchor="middle">{String(cluster.index + 1).padStart(2, '0')}</text>
          <text className="star-cluster-name" x={cluster.x} y={cluster.y - cluster.radius * 1.28 - 6} textAnchor="middle">{cluster.name}</text>
          {!pending && <text className="star-cluster-tally" x={cluster.x} y={cluster.y - cluster.radius * 1.28 + 11} textAnchor="middle">
            {(['stable_for_now', 'consolidating', 'needs_help_now'] as const).filter(key => cluster.counts[key]).map(key => <tspan key={key} className={key} dx="7">● {cluster.counts[key]}</tspan>)}
          </text>}
        </g>)}
      </g>
      <g className="star-links" aria-hidden="true">
        {map.links.map(link => {
          const lit = near.links.has(link.id)
          return <path key={link.id} d={link.path} className={`${link.cross ? 'cross' : ''} ${lit ? 'lit' : ''} ${focusing && !lit ? 'dim' : ''}`} />
        })}
      </g>
      {map.nodes.map(({ node, x, y, r }, index) => {
        const status = statusLabels[node.learning_status], state = pending ? 'pending' : node.learning_status
        const related = !focusing || near.nodes.has(node.id)
        const halo = state === 'stable_for_now' ? 'stable' : state === 'consolidating' ? 'consolidating' : state === 'needs_help_now' ? 'needs' : null
        return <g key={node.id} ref={element => { if (element) buttons.current.set(node.id, element); else buttons.current.delete(node.id) }}
          transform={`translate(${x} ${y})`} role="button" tabIndex={0} aria-pressed={selectedId === node.id}
          aria-label={`${node.name}，${pending ? '正在更新' : `${status.label}，${sourceLabel(node.assessment_source)}`}，查看作答证据`}
          className={`star-node ${state} ${selectedId === node.id ? 'selected' : ''} ${related ? '' : 'dim'} ${named.has(node.id) ? 'named' : ''}`}
          onClick={() => onOpen(node.id)} onKeyDown={event => {
            if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onOpen(node.id) }
            const offset = event.key === 'ArrowRight' || event.key === 'ArrowDown' ? 1 : event.key === 'ArrowLeft' || event.key === 'ArrowUp' ? -1 : 0
            if (offset) { event.preventDefault(); const next = map.nodes[(index + offset + map.nodes.length) % map.nodes.length]; buttons.current.get(next.node.id)?.focus() }
          }}>
          <title>{node.name}</title>
          <circle className="star-hit" r={Math.max(22, r + 12)} />
          {halo && <circle className="star-halo" r={r * 3.4} fill={`url(#${uid}-halo-${halo})`} style={{ animationDelay: `${(index % 9) * 0.35}s` }} />}
          <circle className="star-selected-ring" r={r + 7} />
          <circle className="star-core" r={r} />
          {state === 'stable_for_now' && <circle className="star-spark" r={r * 0.38} />}
          <text className="star-label" y={r + 15} textAnchor="middle">{starLabel(node.name)}</text>
        </g>
      })}
    </svg>
  </div>
}
