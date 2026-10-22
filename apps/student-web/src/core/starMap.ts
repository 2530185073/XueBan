import type { KnowledgeGraphEdge, KnowledgeGraphNode, LearningStatus } from './learning'

/**
 * Constellation layout for the knowledge map.
 *
 * Every chapter is a cluster placed on a ring around the centre; its skills sit on a
 * golden-angle spiral (Vogel) inside the cluster. It is deterministic: the same nodes and
 * edges always give the same coordinates, so a student's map never shuffles between visits.
 * Position carries no meaning about mastery; only the status colour and glow do.
 */
export const STAR_WIDTH = 1200
export const STAR_HEIGHT = 860
const CENTRE = { x: STAR_WIDTH / 2, y: STAR_HEIGHT / 2 }

export interface StarNode { node: KnowledgeGraphNode; x: number; y: number; r: number; chapterIndex: number }
export interface StarCluster { id: string; name: string; x: number; y: number; radius: number; index: number; count: number; counts: Partial<Record<LearningStatus, number>> }
export interface StarLink { id: string; from: string; to: string; path: string; cross: boolean }
export interface StarMap { nodes: StarNode[]; clusters: StarCluster[]; links: StarLink[]; width: number; height: number }

const round = (value: number) => Math.round(value * 10) / 10

/** Visual radius of a star: only evidence count enlarges it; "no judgment" stays small and dim. */
export function starRadius(node: KnowledgeGraphNode): number {
  if (node.learning_status === 'no_judgment') return 7
  return Math.min(15, 9 + Math.min(node.valid_evidence_count, 6))
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
  const ringX = STAR_WIDTH * 0.335, ringY = STAR_HEIGHT * 0.335
  const positioned: StarNode[] = []
  const clusters: StarCluster[] = []
  chapterIds.forEach((id, index) => {
    const members = groups.get(id)!
    // Start at the top and walk clockwise so chapter 1 is always at 12 o'clock.
    const angle = -Math.PI / 2 + (index / chapterIds.length) * Math.PI * 2
    const cx = single ? CENTRE.x : CENTRE.x + Math.cos(angle) * ringX
    const cy = single ? CENTRE.y : CENTRE.y + Math.sin(angle) * ringY
    // One chapter on its own fills the canvas; several share it as separate clusters.
    const radius = single ? Math.min(STAR_HEIGHT * 0.36, Math.max(120, Math.sqrt(members.length) * 62)) : Math.min(118, Math.max(54, Math.sqrt(members.length) * 27))
    const counts: Partial<Record<LearningStatus, number>> = {}
    // Shell packing: 1 star in the core, then rings of 6, 12, 18... so spacing stays even at any size.
    const shells: KnowledgeGraphNode[][] = []
    let left = [...members], cap = 1
    while (left.length) { shells.push(left.slice(0, cap)); left = left.slice(cap); cap = shells.length === 1 ? 6 : cap + 6 }
    shells.forEach((shell, ring) => {
      const rr = ring === 0 ? 0 : (radius * ring) / (shells.length - 1 || 1)
      shell.forEach((node, k) => {
        const theta = (k / shell.length) * Math.PI * 2 + ring * 0.55 + index * 0.7
        positioned.push({ node, x: round(cx + Math.cos(theta) * rr), y: round(cy + Math.sin(theta) * rr * 0.9), r: starRadius(node), chapterIndex: index })
        counts[node.learning_status] = (counts[node.learning_status] ?? 0) + 1
      })
    })
    clusters.push({ id, name: members[0].chapter_name || '其他知识点', x: round(cx), y: round(cy), radius: round(radius), index, count: members.length, counts })
  })
  const byId = new Map(positioned.map(point => [point.node.id, point]))
  const seen = new Set<string>()
  const links: StarLink[] = []
  // Sorted so the drawing order (and therefore overlap of translucent lines) never depends on API order.
  for (const edge of [...edges].sort((x, y) => x.id.localeCompare(y.id))) {
    const key = `${edge.from_id}\0${edge.to_id}`
    const a = byId.get(edge.from_id), b = byId.get(edge.to_id)
    if (edge.relation_type !== 'prerequisite_of' || !a || !b || a === b || seen.has(key)) continue
    seen.add(key)
    // A gentle arc bowed toward the centre keeps long cross-chapter links from crossing the clusters' cores.
    const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2
    const pull = a.chapterIndex === b.chapterIndex ? 0.1 : 0.28
    const qx = mx + (CENTRE.x - mx) * pull, qy = my + (CENTRE.y - my) * pull
    links.push({ id: edge.id, from: edge.from_id, to: edge.to_id, path: `M ${a.x} ${a.y} Q ${round(qx)} ${round(qy)} ${b.x} ${b.y}`, cross: a.chapterIndex !== b.chapterIndex })
  }
  return { nodes: positioned, clusters, links, width: STAR_WIDTH, height: STAR_HEIGHT }
}

/** Nodes and links connected to one star, so a selection can light up its prerequisites and what it leads to. */
export function neighbourhood(map: StarMap, id: string | null | undefined) {
  const nodes = new Set<string>(), links = new Set<string>()
  if (!id) return { nodes, links }
  nodes.add(id)
  for (const link of map.links) {
    if (link.from === id || link.to === id) { links.add(link.id); nodes.add(link.from); nodes.add(link.to) }
  }
  return { nodes, links }
}

/** Short Chinese label that fits beside a star without crowding it. */
export function starLabel(name: string, limit = 7): string {
  const chars = Array.from(name)
  return chars.length <= limit ? name : chars.slice(0, limit).join('') + '…'
}

/**
 * Which stars keep their name visible without hovering: those needing attention, plus the selection and its
 * neighbours. A name is dropped when it would sit on top of one already shown (greedy, attention first), so
 * labels never pile up in a dense cluster; the dropped ones still appear on hover or focus.
 */
export function persistentLabels(map: StarMap, selectedNear: Set<string>, pending: boolean, minDistance = 74): Set<string> {
  const shown = new Set<string>(), placed: StarNode[] = []
  const priority = (n: StarNode) => (selectedNear.has(n.node.id) ? 0 : !pending && n.node.learning_status === 'needs_help_now' ? 1 : 2)
  const candidates = map.nodes.filter(n => priority(n) < 2).sort((a, b) => priority(a) - priority(b) || a.node.sort_order - b.node.sort_order || a.node.id.localeCompare(b.node.id))
  for (const n of candidates) {
    if (placed.some(o => Math.hypot(o.x - n.x, o.y - n.y) < minDistance)) continue
    placed.push(n); shown.add(n.node.id)
  }
  return shown
}
