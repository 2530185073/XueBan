import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import type { AgentContext, KnowledgeDetail, KnowledgeMap, MemoryList, OptionalActivities, PracticeItem, PracticeSession } from '../core/learning'
import { activities, context, detail, map, memory, session } from '../core/learning.fixtures'
import { activityOffers, PENDING_NOTICE } from '../learning'
import { graphMap } from '../core/knowledge.fixtures'
import { AgentTips } from './AgentTips'
import { KnowledgeDetailView, KnowledgeMapView } from './KnowledgeMap'
import { MemoryForm, MemoryItems } from './Preferences'
import { ActivityChooser } from './OptionalActivities'
import { PracticeItemCard, PreviewReading, WrapUp } from './PracticeRunner'

const noop = () => {}
const asMap = (value: unknown) => value as KnowledgeMap

describe('knowledge map views', () => {
  it('renders real chapters and evidence-backed nodes without mastery percentages', () => {
    const html = renderToStaticMarkup(<KnowledgeMapView map={graphMap} onOpen={noop} />)
    for (const text of ['有理数', '代数式', '需要再看看', '还不判断', 'AI 初判', '老师核实', '个知识点有作答记录']) expect(html).toContain(text)
    expect(html).toContain('role="button"'); expect(html).toContain('tabindex="0"'); expect(html).toContain('galaxy-links'); expect(html).toContain('按章节飞行'); expect(html).toContain('按状态筛选')
    expect(html).not.toContain('%'); expect(html).not.toContain('掌握度')
  })
  it('hides stale judgments while the projection is pending, even with old node payloads', () => {
    const html = renderToStaticMarkup(<KnowledgeMapView map={{ ...graphMap, projection: { ...graphMap.projection, pending: true } }} onOpen={noop} />)
    expect(html).toContain(PENDING_NOTICE); expect(html).toContain('正在更新')
    for (const text of ['需要再看看', '阶段性较稳', 'AI 初判，', '老师核实，', '个知识点有作答记录']) expect(html).not.toContain(text)
  })
  it('does not turn legacy states or an unreviewed catalog into a personal graph', () => {
    const html = renderToStaticMarkup(<KnowledgeMapView map={asMap(map)} onOpen={noop} />)
    expect(html).toContain('没有记录，不等于不会'); expect(html).not.toContain('knowledge-graph-node')
  })
  it('drills down to the objective, prerequisites and the student’s own dated evidence', () => {
    const html = renderToStaticMarkup(<KnowledgeDetailView detail={detail as KnowledgeDetail} onBack={noop} />)
    for (const text of ['能按法则计算有理数加减', '相反数与绝对值', '1 + 1 = ?', '数学练习', '当时需要订正', '帮助情况没有记录', '第一次作答', '10月16日']) expect(html).toContain(text)
    expect(html).not.toContain('%')
  })
})

describe('学伴记得 panel', () => {
  it('shows tips with the notice and hides itself without tips', () => {
    const html = renderToStaticMarkup(<AgentTips context={context as AgentContext} />)
    expect(html).toContain('学伴记得'); expect(html).toContain('先自己读题两遍再求助'); expect(html).toContain('不包含答案')
    expect(renderToStaticMarkup(<AgentTips context={{ ...context, tips: [] } as AgentContext} />)).toBe('')
    expect(renderToStaticMarkup(<AgentTips context={null} />)).toBe('')
  })
})

describe('preferences and goals', () => {
  it('offers retract only on the student’s own items and a labelled, bounded form', () => {
    const list = { student_id: 'student_one', notice: 'n', items: [memory, { ...memory, id: 'm2', source: 'staff', confirmed_by: 'teacher', category: 'learning_goal', content: '每天订正一道错题' }] } as MemoryList
    const html = renderToStaticMarkup(<MemoryItems list={list} actorId="actor_test" disabled={false} onRetract={noop} />)
    expect(html.match(/撤回/g)?.length).toBe(2); expect(html).toContain('aria-label="撤回：提示先给图示再给文字"'); expect(html).toContain('老师帮你记下')
    const form = renderToStaticMarkup(<MemoryForm disabled={false} onSubmit={async () => true} />)
    expect(form).toMatch(/<label for="[^"]+">写一个使用学伴的偏好/); expect(form).toContain('maxLength="120"'); expect(form).toContain('<legend>想让学伴记住什么</legend>')
  })
})

describe('optional activities', () => {
  it('renders the calm choice card only from open offers', () => {
    const html = renderToStaticMarkup(<ActivityChooser offers={activityOffers(activities as OptionalActivities)!} busy={false} onStart={noop} />)
    for (const text of ['学校任务完成啦', '可以休息', '举一反三', '有理数加减', '下一课预习', '只读一读', '预习 + 小测']) expect(html).toContain(text)
    expect(html).not.toContain('我愿意试试'); expect(html).not.toContain('%')
    const closed = activityOffers({ ...activities, gate: { open: false, reasons: ['no_extra_budget'], remaining_minutes: 0 } } as OptionalActivities)
    expect(closed).toBeNull()
  })
  it('hides the quiz option when no reviewed quiz items exist', () => {
    const html = renderToStaticMarkup(<ActivityChooser offers={activityOffers({ ...activities, preview: { ...activities.preview, quiz_items_available: 0 } } as OptionalActivities)!} busy={false} onStart={noop} />)
    expect(html).toContain('只读一读'); expect(html).not.toContain('预习 + 小测')
  })
})

describe('practice runner pieces', () => {
  const items = session.items as PracticeItem[]
  it('labels the answer input and offers a hint only for variant items that have one', () => {
    const html = renderToStaticMarkup(<PracticeItemCard item={items[0]} index={0} active hintsAllowed busy={false} onAnswer={async () => true} onHint={noop} />)
    expect(html).toMatch(/<label for="[^"]+">你的答案<\/label>/); expect(html).toContain('inputMode="text"'); expect(html).toContain('给我一点提示')
    expect(renderToStaticMarkup(<PracticeItemCard item={items[0]} index={0} active hintsAllowed={false} busy={false} onAnswer={async () => true} onHint={noop} />)).not.toContain('给我一点提示')
  })
  it('uses A–D radio choices for choice items', () => {
    const html = renderToStaticMarkup(<PracticeItemCard item={{ ...items[0], answer_kind: 'choice' }} index={1} active hintsAllowed={false} busy={false} onAnswer={async () => true} onHint={noop} />)
    expect(html).toContain('<legend>选一个答案</legend>'); expect(html.match(/type="radio"/g)?.length).toBe(4); expect(html).toContain('>D</label>')
  })
  it('shows the solution after answering, with gentle copy for incorrect and unscorable answers', () => {
    expect(renderToStaticMarkup(<PracticeItemCard item={items[1]} index={1} active hintsAllowed busy={false} onAnswer={async () => true} onHint={noop} />)).toContain('异号相加')
    const wrong = renderToStaticMarkup(<PracticeItemCard item={{ ...items[1], response: { answer_text: '7', result: 'incorrect', hint_used: false } }} index={1} active hintsAllowed busy={false} onAnswer={async () => true} onHint={noop} />)
    expect(wrong).toContain('没关系'); expect(wrong).toContain('解析')
    expect(renderToStaticMarkup(<PracticeItemCard item={{ ...items[1], response: { answer_text: '大约两个', result: 'unscorable', hint_used: false } }} index={1} active hintsAllowed busy={false} onAnswer={async () => true} onHint={noop} />)).toContain('暂时没有确认对错')
  })
  it('presents preview targets as reading material and wraps up without debt', () => {
    const reading = renderToStaticMarkup(<PreviewReading targets={[{ id: 'kp-x', name: '去括号解方程', objective: '能去括号解一元一次方程', prerequisites: [{ id: 'kp-y', name: '去括号', learning_status: 'no_judgment' }] }]} />)
    expect(reading).toContain('这些是参考，不是考试'); expect(reading).toContain('去括号解方程'); expect(reading).toContain('还不判断')
    const now = Date.parse('2026-10-16T08:00:00Z'), base = session as PracticeSession
    expect(renderToStaticMarkup(<WrapUp session={{ ...base, status: 'exited' }} now={now} onClose={noop} />)).toContain('不会记欠账')
    expect(renderToStaticMarkup(<WrapUp session={{ ...base, budget_ends_at: '2026-10-16T07:00:00Z' }} now={now} onClose={noop} />)).toContain('今天的时间用完啦')
    expect(renderToStaticMarkup(<WrapUp session={base} now={now} onClose={noop} />)).toBe('')
    expect(renderToStaticMarkup(<WrapUp session={{ ...base, status: 'completed', items: [] }} now={now} onClose={noop} />)).toContain('预习看完啦')
  })
})
