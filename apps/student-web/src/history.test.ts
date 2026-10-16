import { describe, expect, it } from 'vitest'
import examples from '../../../contracts/phase1/examples.json'
import type { HomeworkSession, HomeworkSummary } from './core/types'
import { continueTarget, countsSentence, coverageNote, filterHistory, groupByDay, statusesOf, subjects, UNLABELED_SUBJECT } from './history'

const base = examples.entries.find(x => x.name === 'homework')!.value as HomeworkSession
const hw = (id: string, created_at: string, subject: string | null = null): HomeworkSession => ({ ...structuredClone(base), id, created_at, curriculum: { ...base.curriculum, subject } })
const summary = (id: string, counts: Partial<HomeworkSummary['counts']>, first: string | null = null): HomeworkSummary => {
  const full = { needs_correction: 0, pending_review: 0, candidate_correct: 0, confirmed_correct: 0, ...counts }
  return { homeworkId: id, revision: 3, counts: { total: full.needs_correction + full.pending_review + full.candidate_correct + full.confirmed_correct, ...full }, firstUnresolvedCaseId: first, retired: 0 }
}
// Server order is newest first; Asia/Shanghai is UTC+8.
const items = [hw('homework_c', '2026-10-17T01:00:00Z', '数学'), hw('homework_b', '2026-10-16T16:30:00Z'), hw('homework_a', '2026-10-16T10:00:00Z', '英语'), hw('homework_old', '2026-10-10T02:00:00Z', ' ')]
const summaries = { homework_c: summary('homework_c', { needs_correction: 1, confirmed_correct: 2 }, 'case_c1'), homework_b: summary('homework_b', { pending_review: 2 }, 'case_b1'), homework_a: summary('homework_a', { candidate_correct: 1, confirmed_correct: 3 }) }

describe('history grouping and filters', () => {
  it('groups by local calendar day keeping newest-first order', () => {
    const groups = groupByDay(items, new Date('2026-10-17T04:00:00Z'), 'Asia/Shanghai')
    expect(groups.map(g => [g.label, g.items.map(i => i.id)])).toEqual([['今天', ['homework_c', 'homework_b']], ['昨天', ['homework_a']], [expect.stringContaining('10月10日'), ['homework_old']]])
    expect(groupByDay(items, new Date('2026-10-17T04:00:00Z'), 'UTC').map(g => g.items.length)).toEqual([1, 2, 1])
  })
  it('labels missing or blank subjects 未标注科目 and lists it last', () => {
    expect(subjects(items)).toEqual(['数学', '英语', UNLABELED_SUBJECT])
    expect(filterHistory(items, summaries, { subject: UNLABELED_SUBJECT, status: 'all' }).map(h => h.id)).toEqual(['homework_b', 'homework_old'])
  })
  it('filters by 有待订正 / 待老师核实 / 已完成 using only known counts', () => {
    expect(filterHistory(items, summaries, { subject: null, status: 'needs_correction' }).map(h => h.id)).toEqual(['homework_c'])
    expect(filterHistory(items, summaries, { subject: null, status: 'pending_review' }).map(h => h.id)).toEqual(['homework_b'])
    expect(filterHistory(items, summaries, { subject: null, status: 'done' }).map(h => h.id)).toEqual(['homework_a'])
    expect(filterHistory(items, summaries, { subject: '数学', status: 'done' })).toEqual([])
    expect(statusesOf(summary('x', {}).counts)).toEqual([])
    expect(statusesOf(summary('x', { needs_correction: 1, pending_review: 1 }).counts)).toEqual(['needs_correction', 'pending_review'])
  })
  it('continue jumps to the newest homework with a case to correct, then to verification', () => {
    expect(continueTarget(items, summaries)).toEqual({ homeworkId: 'homework_c', caseId: 'case_c1' })
    expect(continueTarget(items, { ...summaries, homework_c: summary('homework_c', { confirmed_correct: 1 }) })).toEqual({ homeworkId: 'homework_b', caseId: 'case_b1' })
    expect(continueTarget(items, { homework_a: summaries.homework_a })).toBeNull()
  })
  it('reports per-photo counts and never an accuracy percentage', () => {
    const text = countsSentence(summary('x', { needs_correction: 1, pending_review: 2, confirmed_correct: 3 }).counts)
    expect(text).toBe('本次拍到 6 题：需订正 1，待核实 2，已确认正确 3'); expect(text).not.toMatch(/%|正确率/)
    expect(countsSentence(summary('x', { candidate_correct: 2 }).counts)).toContain('AI初判正确 2')
    expect(coverageNote(base.coverage)).toContain('不代表整份作业')
    expect(coverageNote({ ...base.coverage, collection_scope: 'unknown', note: null })).toContain('只统计这次拍到')
    expect(coverageNote({ ...base.coverage, collection_scope: 'declared_complete', note: null })).toBeNull()
  })
})
