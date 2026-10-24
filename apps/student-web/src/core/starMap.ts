import type { KnowledgeGraphEdge, KnowledgeGraphNode, LearningStatus } from './learning'

/** Deterministic atlas geometry. Position never represents mastery or a score. */
export const STAR_WIDTH = 1200
export const STAR_HEIGHT = 800
const CENTRE = { x: STAR_WIDTH / 2, y: STAR_HEIGHT / 2 }

export interface StarNode { node: KnowledgeGraphNode; x: number; y: number; r: number; chapterIndex: number }
export interface StarCluster { id: string; name: string; x: number; y: number; radius: number; index: number; count: number; counts: Partial<Record<LearningStatus, number>> }
export interface StarLink { id: string; from: string; to: string; path: string; cross: boolean }
export interface AmbientStar { x: number; y: number; r: number; opacity: number }
export interface StarMap { nodes: StarNode[]; clusters: StarCluster[]; links: StarLink[]; ambient: AmbientStar[]; width: number; height: number }

const round = (value: number) => Math.round(value * 10) / 10
export const clampZoom = (value: number) => Math.min(3.2, Math.max(0.72, value))

/** Small, quiet stars; evidence changes size, never an invented mastery percentage. */
export function starRadius(node: KnowledgeGraphNode): number {
  if (node.learning_status === 'no_judgment') return 4.5
  return Math.min(9, 5.5 + Math.min(node.valid_evidence_count, 7) * 0.5)
}

/** A fixed ambient field avoids a repeating wallpaper and never reshuffles between renders. */
function ambientField(): AmbientStar[] {
  return Array.from({ length: 110 }, (_, index) => {
    const x = (index * 197.31 + 43) % STAR_WIDTH
    const y = (index * index * 17.17 + index * 71.3 + 29) % STAR_HEIGHT
    return { x: round(x), y: round(y), r: index % 13 === 0 ? 1.25 : index % 5 === 0 ? 0.85 : 0.55, opacity: 0.16 + (index % 7) * 0.055 }
  })
}

export function layoutStarMap(nodes: KnowledgeGraphNode[], edges: KnowledgeGraphEdge[]): StarMap {
  const ordered = [...nodes].sort((a, b) => a.sort_order - b.sort_order || a.id.localeCompare(b.id))
  const groups = new Map<string, KnowledgeGraphNode[]>()
  for (const node of ordered) {
    const key = node.chapter_id ?? 'unassigned'
    groups.set(key, [...(groups.get(key) ?? []), node])
  }
  const chapterIds = [...groups.keys()]
  const single = chapterIds.length === 1
  const ringX = 335, ringY = 245
  const positioned: StarNode[] = []
  const clusters: StarCluster[] = []
  chapterIds.forEach((id, index) => {
    const members = groups.get(id)!
    const angle = -Math.PI / 2 + (index / chapterIds.length) * Math.PI * 2
    const cx = single ? CENTRE.x : CENTRE.x + Math.cos(angle) * ringX
    const cy = single ? CENTRE.y : CENTRE.y + Math.sin(angle) * ringY
    const radius = single ? Math.min(272, Math.max(172, Math.sqrt(members.length) * 66)) : Math.min(102, Math.max(60, Math.sqrt(members.length) * 29))
    const counts: Partial<Record<LearningStatus, number>> = {}
    const shells: KnowledgeGraphNode[][] = []
    let left = [...members], cap = 1
    while (left.length) { shells.push(left.slice(0, cap)); left = left.slice(cap); cap = shells.length === 1 ? 6 : cap + 6 }
    shells.forEach((shell, ring) => {
      const rr = ring === 0 ? 0 : (radius * ring) / (shells.length - 1 || 1)
      shell.forEach((node, k) => {
        const theta = (k / shell.length) * Math.PI * 2 + ring * 0.55 + index * 0.7
        positioned.push({ node, x: round(cx + Math.cos(theta) * rr), y: round(cy + Math.sin(theta) * rr * 0.88), r: starRadius(node), chapterIndex: index })
        counts[node.learning_status] = (counts[node.learning_status] ?? 0) + 1
      })
    })
    clusters.push({ id, name: members[0].chapter_name || '其他知识点', x: round(cx), y: round(cy), radius: round(radius), index, count: members.length, counts })
  })
  const byId = new Map(positioned.map(point => [point.node.id, point]))
  const seen = new Set<string>()
  const links: StarLink[] = []
  for (const edge of [...edges].sort((x, y) => x.id.localeCompare(y.id))) {
    const key = `${edge.from_id}\0${edge.to_id}`
    const a = byId.get(edge.from_id), b = byId.get(edge.to_id)
    if (edge.relation_type !== 'prerequisite_of' || !a || !b || a === b || seen.has(key)) continue
    seen.add(key)
    const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2
    const pull = a.chapterIndex === b.chapterIndex ? 0.08 : 0.24
    const qx = mx + (CENTRE.x - mx) * pull, qy = my + (CENTRE.y - my) * pull
    links.push({ id: edge.id, from: edge.from_id, to: edge.to_id, path: `M ${a.x} ${a.y} Q ${round(qx)} ${round(qy)} ${b.x} ${b.y}`, cross: a.chapterIndex !== b.chapterIndex })
  }
  return { nodes: positioned, clusters, links, ambient: ambientField(), width: STAR_WIDTH, height: STAR_HEIGHT }
}

/** Direct prerequisites and dependents only; selection never invents transitive mastery. */
export function neighbourhood(map: StarMap, id: string | null | undefined) {
  const nodes = new Set<string>(), links = new Set<string>()
  if (!id) return { nodes, links }
  nodes.add(id)
  for (const link of map.links) {
    if (link.from === id || link.to === id) { links.add(link.id); nodes.add(link.from); nodes.add(link.to) }
  }
  return { nodes, links }
}

export function starLabel(name: string, limit = 7): string {
  const chars = Array.from(name)
  return chars.length <= limit ? name : chars.slice(0, limit).join('') + '…'
}

/** Collision-aware persistent labels; a closer view can reveal more names without moving the graph. */
export function persistentLabels(map: StarMap, selectedNear: Set<string>, pending: boolean, minDistance = 72, includeAll = false): Set<string> {
  const shown = new Set<string>(), placed: StarNode[] = []
  const priority = (n: StarNode) => (selectedNear.has(n.node.id) ? 0 : !pending && n.node.learning_status === 'needs_help_now' ? 1 : 2)
  const candidates = map.nodes.filter(n => includeAll || priority(n) < 2).sort((a, b) => priority(a) - priority(b) || a.node.sort_order - b.node.sort_order || a.node.id.localeCompare(b.node.id))
  for (const n of candidates) {
    if (placed.some(o => Math.hypot(o.x - n.x, o.y - n.y) < minDistance)) continue
    placed.push(n); shown.add(n.node.id)
  }
  return shown
}
