import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import KnowledgeDemoPage from './KnowledgeDemoPage'
import { DEMO_EDGES, DEMO_NODES, DEMO_SUBJECTS } from './knowledgeDemoData'
import { isKnowledgeDemoEnabled } from './knowledgeDemoGate'
import { StarMap } from '../components/StarMap'
import { KnowledgeMapPage } from '../components/KnowledgeMap'
import type { Workspace } from '../useStudentWorkspace'
import { layoutStarMap, STAR_HEIGHT, STAR_WIDTH } from '../core/starMap'

describe('local-only knowledge demonstration', () => {
  const gate = { development: true, hostname: '127.0.0.1', requestedStudent: 'student_demo', currentStudent: 'student_demo', grade: 7 }
  it('requires development, loopback, matching authenticated student and grade 7', () => {
    expect(isKnowledgeDemoEnabled(gate)).toBe(true)
    for (const patch of [{ development: false }, { hostname: 'example.com' }, { hostname: '127.0.0.1.example.com' }, { requestedStudent: null }, { requestedStudent: '' }, { currentStudent: null }, { currentStudent: 'student_other' }, { grade: 6 }, { grade: undefined }]) {
      expect(isKnowledgeDemoEnabled({ ...gate, ...patch })).toBe(false)
    }
  })
  it('covers only the seven requested subjects with independently marked synthetic nodes', () => {
    expect(DEMO_SUBJECTS.map(subject => subject.name).sort()).toEqual(['数学', '语文', '英语', '历史', '道德与法治', '地理', '生物'].sort())
    expect(DEMO_SUBJECTS.find(subject => subject.name === '数学')?.publisher).toBe('北京师范大学出版社')
    expect(DEMO_SUBJECTS.find(subject => subject.name === '生物')?.publisher).toBe('江苏凤凰科学技术出版社')
    expect(DEMO_NODES).toHaveLength(28)
    expect(new Set(DEMO_NODES.map(node => node.id)).size).toBe(28)
    for (const subject of DEMO_SUBJECTS) {
      expect(subject.topics).toHaveLength(4)
      expect(new Set(subject.topics.map(topic => topic.status)).size).toBe(4)
      for (const topic of subject.topics) {
        expect(topic.id).toMatch(/^demo_/)
        if (topic.status === 'no_judgment') expect(topic.answer).toBe('')
        else { expect(topic.question).not.toBe(''); expect(topic.correctAnswer).not.toBe(''); expect(topic.steps.length).toBeGreaterThan(0) }
      }
    }
    expect(DEMO_NODES.every(node => node.assessment_source === 'none' && node.model_evidence_count === 0 && node.staff_verified_evidence_count === 0)).toBe(true)
  })
  it('uses only bounded within-subject prerequisite illustrations', () => {
    for (const edge of DEMO_EDGES) {
      const from = DEMO_NODES.find(node => node.id === edge.from_id)!, to = DEMO_NODES.find(node => node.id === edge.to_id)!
      expect(from).toBeTruthy(); expect(to).toBeTruthy()
      expect(from.subject).toBe(to.subject); expect(from.id).not.toBe(to.id)
    }
    for (const node of layoutStarMap(DEMO_NODES, DEMO_EDGES).nodes) {
      expect(node.x).toBeGreaterThan(0); expect(node.x).toBeLessThan(STAR_WIDTH)
      expect(node.y).toBeGreaterThan(0); expect(node.y).toBeLessThan(STAR_HEIGHT)
    }
  })
  it('labels the whole page and map as synthetic without model/teacher verification claims', () => {
    const html = renderToStaticMarkup(<KnowledgeDemoPage onExit={() => {}} />)
    for (const text of ['模拟演示', '退出演示', '不代表该学生的真实水平', '28 个模拟知识节点', '模拟作答']) expect(html).toContain(text)
    for (const text of ['个真实知识节点', '老师核实', '根据 AI 初判', '保存教材', '提交答案', '生成练习']) expect(html).not.toContain(text)
    const live = renderToStaticMarkup(<StarMap nodes={DEMO_NODES} edges={DEMO_EDGES} pending={false} onOpen={() => {}} />)
    expect(live).toContain('28 个真实知识节点')
    expect(live).not.toContain('模拟状态')
  })
  it('keeps the normal empty map unchanged when the demo is not explicitly requested', () => {
    const workspace = { studentId: 'student_one', client: {}, busy: '', recordVersion: 0 } as unknown as Workspace
    const html = renderToStaticMarkup(<KnowledgeMapPage workspace={workspace} map={{ student_id: 'student_one', grade: 7, states: [], chapters: [], projection: { pending: false, projected_at: null }, notice: '' }} loading={false} loadError="" onRefresh={() => {}} />)
    expect(html).toContain('这张地图，还在慢慢准备')
    expect(html).not.toContain('模拟演示')
  })
})
