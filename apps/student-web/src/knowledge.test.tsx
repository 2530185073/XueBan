import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { graphDetail, graphEdges, graphMap, graphNodes, syntheticBook } from './core/knowledge.fixtures'
import { isKnowledgeDetail, isKnowledgeMap } from './core/learning'
import { KnowledgeDetailView, KnowledgeMapView } from './components/KnowledgeMap'
import { knowledgeChapters, knowledgeNodes, layoutKnowledgeGraph, nodeNameLines } from './learning'

const noop = () => {}

describe('current student knowledge graph contract', () => {
  it('accepts nullable edge metadata and unknown legacy evidence sources', () => {
    expect(isKnowledgeMap(graphMap)).toBe(true)
    expect(isKnowledgeMap({ ...graphMap, book_identity: null, source_note: null })).toBe(true)
    expect(isKnowledgeDetail({ ...graphDetail, evidence: graphDetail.evidence.map(e => ({ ...e, source_role: 'unknown' })) })).toBe(true)
    expect(isKnowledgeMap({ ...graphMap, edges: [{ ...graphEdges[0], relation_type: 'guessed_weakness' }] })).toBe(false)
    expect(isKnowledgeMap({ ...graphMap, nodes: [{ ...graphNodes[0], valid_evidence_count: -1 }] })).toBe(false)
    expect(isKnowledgeMap({ ...graphMap, projection: { ...graphMap.projection, state_version: 'old' } })).toBe(false)
    expect(isKnowledgeDetail({ ...graphDetail, projection: { pending: 'yes' } })).toBe(false)
  })
  it('blocks unreviewed graphs and masks every stale conclusion without mutating source records', () => {
    expect(knowledgeNodes({ ...graphMap, graph_review_status: 'pilot_seed_unreviewed' })).toEqual([])
    expect(knowledgeNodes({ ...graphMap, graph_review_status: undefined })).toEqual([])
    const nodes = knowledgeNodes({ ...graphMap, projection: { ...graphMap.projection, pending: true } })
    expect(nodes).toHaveLength(graphNodes.length)
    expect(nodes.every(n => n.learning_status === 'no_judgment' && n.valid_evidence_count === 0 && n.model_evidence_count === 0 && n.staff_verified_evidence_count === 0 && n.assessment_source === 'none' && !n.reason_codes.length)).toBe(true)
    expect(graphNodes[0].learning_status).toBe('stable_for_now')
  })
  it('groups by actual chapter ids and leaves unsupported node types out', () => {
    expect(knowledgeChapters(knowledgeNodes(graphMap)).map(ch => [ch.id, ch.nodes.length])).toEqual([['ch-rational', 5], ['ch-algebra', 2]])
    expect(knowledgeNodes({ ...graphMap, nodes: [{ ...graphNodes[0], node_type: 'chapter' }] })).toEqual([])
  })
})

describe('graph layout', () => {
  it('draws only supplied relationships with both visible endpoints, without duplicates or self links', () => {
    const nodes = graphNodes.slice(0, 3)
    const result = layoutKnowledgeGraph(nodes, [...graphEdges, graphEdges[0], { ...graphEdges[0], id: 'self', to_id: graphEdges[0].from_id }])
    expect(result.positions.map(p => p.node.id)).toEqual(nodes.map(n => n.id))
    expect(result.paths.map(p => p.edge.id)).toEqual(['edge-0', 'edge-1'])
    expect(result.positions[0].y).toBeLessThan(result.positions[1].y)
    expect(layoutKnowledgeGraph(nodes, []).paths).toEqual([])
    expect(result.positions.every(p => p.x >= 0 && p.x + 192 <= result.width)).toBe(true)
  })
  it('stays bounded on cycles and empty chapters instead of propagating learning status', () => {
    const result = layoutKnowledgeGraph(graphNodes.slice(0, 2), [graphEdges[0], { ...graphEdges[0], id: 'cycle', from_id: graphEdges[0].to_id, to_id: graphEdges[0].from_id }])
    expect(result.positions).toHaveLength(2); expect(Number.isFinite(result.height)).toBe(true)
    expect(result.positions[0].node.learning_status).toBe('stable_for_now')
    expect(layoutKnowledgeGraph([], []).positions).toEqual([])
    expect(nodeNameLines('认识有理数')).toEqual(['认识有理数'])
    expect(nodeNameLines('这是一个超过二十个汉字的非常长的知识点测试标题名称').every(line => Array.from(line).length <= 10)).toBe(true)
  })
})

describe('evidence detail safety', () => {
  it('labels AI evidence and provides a real-homework action only with bound ids', () => {
    const html = renderToStaticMarkup(<KnowledgeDetailView detail={graphDetail} onBack={noop} onOpenHomework={noop} />)
    expect(html).toContain('根据 AI 初判，尚未经老师核实')
    expect(html).toContain('查看原题与订正'); expect(html).toContain('(-3) + 5 = ?')
    const noIds = { ...graphDetail, evidence: graphDetail.evidence.map(e => ({ ...e, homework_id: undefined })) }
    expect(renderToStaticMarkup(<KnowledgeDetailView detail={noIds} onBack={noop} onOpenHomework={noop} />)).not.toContain('查看原题与订正')
  })
  it('pending in either map or detail hides stale evidence, source and prerequisite judgments', () => {
    for (const fromDetail of [false, true]) {
      const html = renderToStaticMarkup(<KnowledgeDetailView detail={fromDetail ? { ...graphDetail, projection: { ...graphMap.projection, pending: true } } : graphDetail} pending={!fromDetail} onBack={noop} onOpenHomework={noop} />)
      for (const text of ['(-3) + 5 = ?', '需要再看看', 'AI 初判', '查看原题与订正']) expect(html).not.toContain(text)
      expect(html).toContain('正在整理新的作答记录')
    }
  })
  it('never labels absent or unknown legacy provenance as teacher confirmation', () => {
    const html = renderToStaticMarkup(<KnowledgeDetailView detail={{ ...graphDetail, state: null, evidence: graphDetail.evidence.map(e => ({ ...e, source_role: 'unknown' })) }} onBack={noop} />)
    expect(html).toContain('来源待核实'); expect(html).not.toContain('老师核实')
  })
})

it('shows source-verified AI graph with its real review provenance', () => {
  const map = { ...graphMap, graph_review_status: 'source_verified_ai_reviewed', book_identity: syntheticBook, source_note: syntheticBook.source_note }
  expect(knowledgeNodes(map)).toHaveLength(graphNodes.length)
  const html = renderToStaticMarkup(<KnowledgeMapView map={map} onOpen={noop} />)
  expect(html).toContain('目录来源已核对，知识点关系由 AI 整理')
  expect(html).toContain(syntheticBook.source_note!)
})
