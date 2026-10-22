import { describe, expect, it } from 'vitest'
import type { KnowledgeGraphEdge, KnowledgeGraphNode, LearningStatus } from './learning'
import { layoutStarMap, neighbourhood, persistentLabels, starLabel, starRadius, STAR_HEIGHT, STAR_WIDTH } from './starMap'

const node = (id: string, chapter: string, order: number, status: LearningStatus = 'no_judgment', evidence = 0): KnowledgeGraphNode => ({
  id, node_type: 'skill', parent_id: chapter, name: '节点' + id, definition: null, observable_objective: null, sort_order: order, subject: '数学',
  chapter_id: chapter, chapter_name: '章' + chapter, learning_status: status, reason_codes: [], valid_evidence_count: evidence, assessment_source: 'none',
} as KnowledgeGraphNode)
const edge = (id: string, from: string, to: string): KnowledgeGraphEdge => ({ id, from_id: from, to_id: to, relation_type: 'prerequisite_of', scope: null, rationale: null })
const nodes = ['a', 'b', 'c', 'd', 'e', 'f'].map((id, i) => node(id, i < 3 ? 'c1' : 'c2', i))
const edges = [edge('e1', 'a', 'b'), edge('e2', 'b', 'c'), edge('e3', 'c', 'd')]

describe('star map layout', () => {
  it('is deterministic: the same input gives identical coordinates', () => {
    expect(layoutStarMap(nodes, edges)).toEqual(layoutStarMap([...nodes].reverse(), [...edges].reverse()))
  })
  it('places every node inside the canvas, and no two stars overlap', () => {
    const many = Array.from({ length: 75 }, (_, i) => node('n' + i, 'ch' + (i % 6), i, 'stable_for_now', 3))
    const { nodes: placed } = layoutStarMap(many, [])
    expect(placed).toHaveLength(75)
    for (const p of placed) { expect(p.x).toBeGreaterThan(0); expect(p.x).toBeLessThan(STAR_WIDTH); expect(p.y).toBeGreaterThan(0); expect(p.y).toBeLessThan(STAR_HEIGHT) }
    for (let i = 0; i < placed.length; i++) for (let j = i + 1; j < placed.length; j++) {
      expect(Math.hypot(placed[i].x - placed[j].x, placed[i].y - placed[j].y)).toBeGreaterThan(placed[i].r * 0.9)
    }
  })
  it('groups chapters into clusters with status counts, chapter 1 at the top', () => {
    const { clusters } = layoutStarMap(nodes.map((n, i) => ({ ...n, learning_status: i % 2 ? 'consolidating' : 'stable_for_now' as LearningStatus })), edges)
    expect(clusters.map(c => c.name)).toEqual(['章c1', '章c2'])
    expect(clusters[0].y).toBeLessThan(clusters[1].y)
    expect(clusters[0].count).toBe(3); expect(clusters[0].counts.stable_for_now).toBe(2)
  })
  it('keeps real prerequisite links only: one per pair, no self-links, unknown ends or other relation types', () => {
    const noisy = [...edges, edge('dup', 'a', 'b'), edge('self', 'a', 'a'), edge('ghost', 'a', 'zzz'), { ...edge('other', 'a', 'c'), relation_type: 'supports' } as unknown as KnowledgeGraphEdge]
    const pairs = (list: KnowledgeGraphEdge[]) => layoutStarMap(nodes, list).links.map(l => `${l.from}>${l.to}`).sort()
    expect(pairs(noisy)).toEqual(['a>b', 'b>c', 'c>d'])
    expect(layoutStarMap(nodes, noisy).links.map(l => l.id)).toEqual(layoutStarMap(nodes, [...noisy].reverse()).links.map(l => l.id))
  })
  it('marks links that cross chapters', () => {
    const { links } = layoutStarMap(nodes, edges)
    expect(links.find(l => l.id === 'e1')!.cross).toBe(false); expect(links.find(l => l.id === 'e3')!.cross).toBe(true)
  })
  it('handles empty and single-chapter input without throwing', () => {
    expect(layoutStarMap([], [])).toMatchObject({ nodes: [], clusters: [], links: [] })
    const solo = layoutStarMap(nodes.slice(0, 3), edges)
    expect(solo.clusters).toHaveLength(1); expect(solo.clusters[0].x).toBe(STAR_WIDTH / 2)
  })
})

describe('star map semantics', () => {
  it('size reflects evidence count only, never a score; no-judgment stays small', () => {
    expect(starRadius(node('x', 'c', 0, 'no_judgment', 9))).toBe(7)
    expect(starRadius(node('x', 'c', 0, 'stable_for_now', 0))).toBe(9)
    expect(starRadius(node('x', 'c', 0, 'stable_for_now', 99))).toBe(15)
  })
  it('selection lights up only its direct neighbours', () => {
    const map = layoutStarMap(nodes, edges)
    const near = neighbourhood(map, 'b')
    expect([...near.nodes].sort()).toEqual(['a', 'b', 'c']); expect([...near.links].sort()).toEqual(['e1', 'e2'])
    expect(neighbourhood(map, null).nodes.size).toBe(0)
  })
  it('shortens long names without breaking short ones', () => {
    expect(starLabel('相反数与绝对值')).toBe('相反数与绝对值')
    expect(starLabel('从数量关系建立一元一次方程')).toBe('从数量关系建立…')
  })
})

describe('persistent labels', () => {
  const needy = Array.from({ length: 12 }, (_, i) => node('n' + i, 'c1', i, 'needs_help_now', 1))
  it('names attention stars but never stacks two labels closer than the minimum distance', () => {
    const map = layoutStarMap(needy, [])
    const shown = persistentLabels(map, new Set(), false)
    expect(shown.size).toBeGreaterThan(0)
    const pts = map.nodes.filter(n => shown.has(n.node.id))
    for (let i = 0; i < pts.length; i++) for (let j = i + 1; j < pts.length; j++) expect(Math.hypot(pts[i].x - pts[j].x, pts[i].y - pts[j].y)).toBeGreaterThanOrEqual(74)
  })
  it('names nothing while the projection is pending, and quiet stars stay unnamed', () => {
    expect(persistentLabels(layoutStarMap(needy, []), new Set(), true).size).toBe(0)
    const quiet = Array.from({ length: 5 }, (_, i) => node('q' + i, 'c1', i, 'stable_for_now', 3))
    expect(persistentLabels(layoutStarMap(quiet, []), new Set(), false).size).toBe(0)
  })
  it('always keeps the selected star named, ahead of attention stars', () => {
    const map = layoutStarMap(needy, [])
    const target = map.nodes[5].node.id
    expect(persistentLabels(map, new Set([target]), false).has(target)).toBe(true)
  })
  it('is deterministic', () => {
    const map = layoutStarMap(needy, [])
    expect([...persistentLabels(map, new Set(), false)]).toEqual([...persistentLabels(layoutStarMap([...needy].reverse(), []), new Set(), false)])
  })
})
