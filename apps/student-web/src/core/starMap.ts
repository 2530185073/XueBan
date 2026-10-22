import type { KnowledgeGraphEdge, KnowledgeGraphNode, LearningStatus } from './learning'

/**
 * Constellation layout for the knowledge map.
 *
 * Every chapter is a cosmic cluster placed along an elliptical orbit around the centre;
 * its skills sit on a natural golden-angle spiral (Vogel) inside the cluster.
 * It is 100% deterministic: identical nodes and edges always produce identical coordinates.
 * Position represents curriculum topology; starlight luminescence and color indicate evidence.
 */
export const STAR_WIDTH = 1200
export const STAR_HEIGHT = 860
const CENTRE = { x: STAR_WIDTH / 2, y: STAR_HEIGHT / 2 }

export interface StarNode {
  node: KnowledgeGraphNode
  x: number
  y: number
  r: number
  chapterIndex: number
}

export interface StarCluster {
  id: string
  name: string
  x: number
  y: number
  radius: number
  index: number
  count: number
  counts: Partial<Record<LearningStatus, number>>
}

export interface StarLink {
  id: string
  from: string
  to: string
  path: string
  cross: boolean
  midX: number
  midY: number
}

export interface StarMap {
  nodes: StarNode[]
  clusters: StarCluster[]
  links: StarLink[]
  width: number
  height: number
}

export interface StarNeighbourhood {
  nodes: Set<string>
  links: Set<string>
  upstreamNodes: Set<string>
  downstreamNodes: Set<string>
  upstreamLinks: Set<string>
  downstreamLinks: Set<string>
}

export interface DemoStoryStep {
  id: string
  stageName: string
  title: string
  subtitle: string
  focalNodeId?: string
  chapterId?: string
  script: string
  keyTakeaway: string
  filterStatus?: LearningStatus | 'all'
}

export const DEMO_STORY_STEPS: DemoStoryStep[] = [
  {
    id: 'overview',
    stageName: '第 1 步 · 全景概览',
    title: '知识全景星图',
    subtitle: '真实作业照亮每一颗星 · 拒绝冰冷分数',
    filterStatus: 'all',
    script: '这是一个学生的个人知识地图，不是一张冷冰冰的分数表。全册 75 个知识点中，已有 58 个留下了真实作答足迹。亮点代表有真实作业证明，暗的代表还没学到——没有记录不等于不会，绝不给孩子贴负面标签。',
    keyTakeaway: '全景呈现 · 33 较稳 · 18 巩固中 · 7 需重点关注 · 17 尚未涉及',
  },
  {
    id: 'weakness-focus',
    stageName: '第 2 步 · 精准卡点',
    title: '直击薄弱症结',
    subtitle: '拒绝笼统“粗心” · 精确定位到具体卡点',
    focalNodeId: 'draft-pep7-operation-order',
    chapterId: 'ch-operations',
    filterStatus: 'needs_help_now',
    script: '平时家长常听学校老师说孩子“计算粗心”，但究竟粗心在哪里？看第二章这颗琥珀色的星——「有理数运算顺序」。系统从真实作答中精准发现：孩子并非不会算，而是在除法转乘法时的负号处理规律性混淆。',
    keyTakeaway: '精确定位到除法转乘倒数与负号结合的卡点，不扣“粗心”大帽子',
  },
  {
    id: 'prerequisite-flow',
    stageName: '第 3 步 · 追溯脉络',
    title: '知识依赖图谱',
    subtitle: '顺藤摸瓜 · 向上看根源，向下防断层',
    focalNodeId: 'draft-pep7-operation-order',
    filterStatus: 'all',
    script: '知识点不是孤立的。选中后光流亮起：向上看，它依赖第一周的「有理数加减」和「乘方与负数底数」——如果根底未吃透，运算顺序必然反复出错；向下看，它还会直接影响第四章的「整式去括号」。这就是必须在此刻帮孩子弄明白的原因！',
    keyTakeaway: '能量光流流动 · 追溯前置基石 · 预判后置影响',
  },
  {
    id: 'evidence-trace',
    stageName: '第 4 步 · 原卷证据',
    title: '老师核实闭环',
    subtitle: '每条结论均由坐班老师逐题核实验证',
    focalNodeId: 'draft-pep7-operation-order',
    script: '右侧就是孩子的成长证据清单。10 月 20 日第一次作答卡在 (-18) ÷ 9，老师在现场核实并记录了错因。点击“查看原题与订正”，可以瞬间调出当时那页作业的原卷照片与红笔订正迹线。每一个字都有据可依！',
    keyTakeaway: '真实笔迹原卷 · 坐班老师逐题确认 · 告别凭空臆测',
  },
  {
    id: 'growth-verification',
    stageName: '第 5 步 · 见证成长',
    title: '扎实稳固蜕变',
    subtitle: '从错题订正到跨日期独立做对的进步',
    focalNodeId: 'draft-pep7-rational-add-sub',
    filterStatus: 'stable_for_now',
    script: '托管班如何帮孩子把知识学扎实？看这颗绿色的星——孩子最初做错，经过老师现场提示订正，再在不同日期的学校新作业中连续两次独立做对，系统才评为“阶段性较稳”。一次做对不算，真正走稳每一步！',
    keyTakeaway: '先错 ➔ 订正 ➔ 跨日期独立新题做对 ➔ 评为阶段性较稳',
  },
]

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
    const count = members.length

    members.forEach((node, k) => {
      let x: number, y: number
      if (k === 0) {
        x = cx
        y = cy
      } else {
        // Natural Vogel spiral (golden angle) for organic, evenly spaced open star clusters
        const theta = k * 2.399963229728653 + index * 0.85
        const dist = Math.sqrt(k / count) * (radius * 0.86) + 12
        x = cx + Math.cos(theta) * dist
        y = cy + Math.sin(theta) * dist * 0.92
      }
      positioned.push({ node, x: round(x), y: round(y), r: starRadius(node), chapterIndex: index })
      counts[node.learning_status] = (counts[node.learning_status] ?? 0) + 1
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
    links.push({
      id: edge.id,
      from: edge.from_id,
      to: edge.to_id,
      path: `M ${a.x} ${a.y} Q ${round(qx)} ${round(qy)} ${b.x} ${b.y}`,
      cross: a.chapterIndex !== b.chapterIndex,
      midX: round(qx),
      midY: round(qy),
    })
  }

  return { nodes: positioned, clusters, links, width: STAR_WIDTH, height: STAR_HEIGHT }
}

/** Nodes and links connected to one star, separating upstream prerequisites and downstream unlocks. */
export function neighbourhood(map: StarMap, id: string | null | undefined): StarNeighbourhood {
  const nodes = new Set<string>(), links = new Set<string>()
  const upstreamNodes = new Set<string>(), downstreamNodes = new Set<string>()
  const upstreamLinks = new Set<string>(), downstreamLinks = new Set<string>()
  if (!id) return { nodes, links, upstreamNodes, downstreamNodes, upstreamLinks, downstreamLinks }
  nodes.add(id)
  for (const link of map.links) {
    if (link.to === id) {
      // link.from is a prerequisite of id (upstream)
      links.add(link.id)
      nodes.add(link.from)
      upstreamLinks.add(link.id)
      upstreamNodes.add(link.from)
    } else if (link.from === id) {
      // id is a prerequisite of link.to (downstream)
      links.add(link.id)
      nodes.add(link.to)
      downstreamLinks.add(link.id)
      downstreamNodes.add(link.to)
    }
  }
  return { nodes, links, upstreamNodes, downstreamNodes, upstreamLinks, downstreamLinks }
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
    placed.push(n)
    shown.add(n.node.id)
  }
  return shown
}
