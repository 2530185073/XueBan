import { describe, expect, it } from 'vitest'
import { StudentError } from './core/errors'
import type { AgentContext, Memory, OptionalActivities, VariantOffer } from './core/learning'
import { activities, context, map, memory } from './core/learning.fixtures'
import { activityOffers, answerFeedback, chapterCounts, clampCount, dayLabel, effectiveStatus, exitChoices, groupStates, hasKnowledgeMap, practiceMessage, profileMemories, reasonText, remainingLabel, resultLabels, statusLabels, variantDefault, variantMax, visibleTips, wrapUps } from './learning'

const offers = structuredClone(activities) as OptionalActivities
const variant = (available_items: number): VariantOffer => ({ knowledge_id: 'kp-a', name: '去括号', learning_status: 'consolidating', available_items })

describe('learning labels', () => {
  it('maps every status to the agreed friendly label', () => {
    expect(Object.fromEntries(Object.entries(statusLabels).map(([k, v]) => [k, v.label]))).toEqual({ needs_help_now: '需要再看看', consolidating: '正在巩固', stable_for_now: '阶段性较稳', no_judgment: '还不判断' })
  })
  it('explains reasons in plain words and falls back per status for unknown codes', () => {
    expect(reasonText('needs_help_now', ['single_error_needs_diagnosis'])).toContain('和老师一起看看')
    expect(reasonText('consolidating', ['independent_success_awaiting_more_dates'])).toContain('过几天')
    expect(reasonText('stable_for_now', [])).toContain('不同的日子')
    expect(reasonText('no_judgment', ['future_code'])).toBe('资料还不够，先不下判断。')
  })
  it('never uses percentages, scores or rankings in any copy', () => {
    const copy = JSON.stringify([statusLabels, resultLabels, answerFeedback, wrapUps, exitChoices, ['repeated_errors', 'assisted_or_unverified_only', 'no_scorable_evidence', 'corrected_with_help_awaiting_new_school_item'].map(code => reasonText('no_judgment', [code]))])
    for (const absent of ['%', '正确率', '掌握度', '排名', '分数', '错过']) expect(copy).not.toContain(absent)
    expect(wrapUps.exited.text).toContain('不会记欠账'); expect(answerFeedback.unscorable.title).toBe('没法自动判断，可以对照解析')
  })
})

describe('knowledge map helpers', () => {
  it('shows the entry only when the server has states', () => {
    expect(hasKnowledgeMap(map as never)).toBe(true)
    expect(hasKnowledgeMap({ ...map, states: [] } as never)).toBe(false)
    expect(hasKnowledgeMap({ ...map, states: [], projection: { ...map.projection, pending: true } } as never)).toBe(false)
    expect(hasKnowledgeMap(null)).toBe(false)
  })
  it('lists non-zero chapter counts in status order and groups knowledge by chapter', () => {
    expect(chapterCounts({ chapter_id: 'c', name: '有理数', objectives: 5, status_counts: { no_judgment: 2, stable_for_now: 1, consolidating: 0, needs_help_now: 2 } }).map(c => `${c.label} ${c.count}`)).toEqual(['需要再看看 2', '阶段性较稳 1', '还不判断 2'])
    const groups = groupStates([{ ...map.states[0] }, { ...map.states[0], knowledge_id: 'kp-b', chapter_id: null, chapter_name: null }, { ...map.states[0], knowledge_id: 'kp-c' }] as never)
    expect(groups.map(g => [g.name, g.states.length])).toEqual([['有理数', 2], ['其他知识点', 1]])
  })
})

describe('学伴记得 tips', () => {
  it('shows at most three tips without the redundant lead-in, and nothing when there are none', () => {
    const many = { ...context, tips: Array.from({ length: 5 }, (_, i) => ({ ...context.tips[0], memory_id: `m${i}`, text: `学伴记得：第 ${i} 条` })) } as AgentContext
    expect(visibleTips(many).map(t => t.display)).toEqual(['第 0 条', '第 1 条', '第 2 条'])
    expect(visibleTips({ ...context, tips: [] } as AgentContext)).toEqual([])
    expect(visibleTips(null)).toEqual([])
  })
})

describe('preferences', () => {
  it('lists only confirmed preferences, goals and agreements, and marks the student’s own as retractable', () => {
    const items = [memory, { ...memory, id: 'm2', source: 'staff', confirmed_by: 'teacher', category: 'learning_goal' }, { ...memory, id: 'm3', category: 'recurring_difficulty' }, { ...memory, id: 'm4', status: 'retracted' }, { ...memory, id: 'm5', category: 'support_agreement', source: 'staff' }] as Memory[]
    expect(profileMemories(items, 'actor_test').map(m => [m.id, m.own])).toEqual([['memory_1', true], ['m2', false], ['m5', false]])
    expect(profileMemories(items, null).every(m => !m.own)).toBe(true)
    expect(profileMemories(items, 'someone_else').some(m => m.own)).toBe(false)
  })
})

describe('optional activity gate', () => {
  it('offers nothing when the gate is closed, even if the server listed options', () => {
    expect(activityOffers({ ...offers, gate: { open: false, reasons: ['capacity_not_observed'], remaining_minutes: 10 } })).toBeNull()
    expect(activityOffers({ ...offers, gate: { open: true, reasons: [], remaining_minutes: 0 } })).toBeNull()
    expect(activityOffers(null)).toBeNull()
  })
  it('offers nothing when the gate is open but there is nothing to choose', () => {
    expect(activityOffers({ ...offers, variants: [variant(0)], preview: null })).toBeNull()
    expect(activityOffers({ ...offers, variants: [{ ...variant(2), learning_status: 'stable_for_now' }], preview: { knowledge_ids: [], names: [], quiz_items_available: 0 } })).toBeNull()
  })
  it('keeps variants with reviewed items and the preview when open', () => {
    const open = activityOffers(offers)!
    expect(open.variants.map(v => v.knowledge_id)).toEqual(['kp-rational-add-sub']); expect(open.preview?.names).toEqual(['去括号解一元一次方程']); expect(open.remainingMinutes).toBe(10)
  })
  it('bounds the variant count to 1–3 and to the reviewed items, defaulting to 2', () => {
    expect([variantMax(variant(5)), variantDefault(variant(5))]).toEqual([3, 2])
    expect([variantMax(variant(1)), variantDefault(variant(1))]).toEqual([1, 1])
    expect([clampCount(0, variant(5)), clampCount(9, variant(5)), clampCount(3, variant(2)), clampCount(Number.NaN, variant(5))]).toEqual([1, 3, 2, 2])
  })
})

describe('session time', () => {
  const now = Date.parse('2026-10-16T08:00:00Z')
  it('shows the year only for dates outside the current year', () => {
    expect(dayLabel('2026-10-16T08:00:00Z', new Date(now))).toBe('10月16日')
    expect(dayLabel('2027-10-16T08:00:00Z', new Date(now))).toBe('2027年10月16日')
    expect(dayLabel('later', new Date(now))).toBe('日期待确认')
  })
  it('formats remaining time calmly', () => {
    expect(remainingLabel('2026-10-16T08:10:00Z', now)).toBe('约 10 分钟')
    expect(remainingLabel('2026-10-16T08:01:29Z', now)).toBe('约 1 分钟')
    expect(remainingLabel('2026-10-16T08:00:30Z', now)).toBe('不到 1 分钟')
    expect(remainingLabel('2026-10-16T07:59:00Z', now)).toBe('时间到了')
    expect(remainingLabel('not a date', now)).toBe('时间到了')
  })
  it('treats an active session past its budget as time_up before the server notices', () => {
    expect(effectiveStatus({ status: 'active', budget_ends_at: '2026-10-16T07:59:59Z' }, now)).toBe('time_up')
    expect(effectiveStatus({ status: 'active', budget_ends_at: '2026-10-16T08:05:00Z' }, now)).toBe('active')
    expect(effectiveStatus({ status: 'exited', budget_ends_at: '2026-10-16T08:05:00Z' }, now)).toBe('exited')
  })
  it('maps the start conflicts to kind messages', () => {
    for (const code of ['optional_gate_closed', 'practice_not_needed', 'course_unknown', 'no_reviewed_items', 'practice_active', 'hints_disabled']) expect(practiceMessage(new StudentError(code, 409), 'x')).not.toBe(new StudentError(code, 409).message)
    expect(practiceMessage(new StudentError('optional_gate_closed', 409), 'x')).toContain('今天就到这里也很好')
    expect(practiceMessage(new Error('boom'), '这次没有开始。')).toBe('这次没有开始。')
  })
})
