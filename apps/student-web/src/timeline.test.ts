import { describe, expect, it } from 'vitest'
import examples from '../../../contracts/phase1/examples.json'
import type { Attempt, CaseRecord, CorrectionCase, Decision, HelpRequest, Hint, Review } from './core/types'
import { attemptLabels, buildTimeline, currentHelp } from './timeline'

const fixture = <T>(name: string) => structuredClone(examples.entries.find(x => x.name === name)!.value) as T
const at = (minute: number) => `2026-10-16T08:${String(minute).padStart(2, '0')}:00Z`

function record(): CaseRecord {
  const c = fixture<CorrectionCase>('resolved_case')
  const original = { ...fixture<Attempt>('original_attempt'), created_at: at(0) }
  const correction = { ...fixture<Attempt>('correction_across_question_revision'), created_at: at(20) }
  const old = { ...fixture<Decision>('stale_old_decision'), created_at: at(5) }
  const candidate = { ...fixture<Decision>('model_candidate'), created_at: at(25), validity: 'stale' as const }
  const confirmed = { ...fixture<Decision>('staff_confirmed'), created_at: at(40), confirmed_at: at(40) }
  const hint = { ...fixture<Hint>('shown_but_invalidated_hint'), first_shown_at: at(10) }
  const unseen: Hint = { ...hint, id: 'hint_unseen', first_shown_at: null, validity: 'active' }
  const help = { ...fixture<HelpRequest>('resolved_help'), kind: 'appeal' as const, created_at: at(30) }
  const review = { ...fixture<Review>('staff_review'), created_at: at(40) }
  return {
    case: c,
    attempts: [{ record: correction, current_revision: 1, currently_effective: true }, { record: original, current_revision: 1, currently_effective: false }],
    checks: [],
    decisions: [{ record: confirmed, current_revision: 1, currently_effective: true }, { record: candidate, current_revision: 2, currently_effective: false }, { record: old, current_revision: 2, currently_effective: false }],
    hints: [hint, unseen], help: [help], reviews: [review],
  }
}

describe('per-case version timeline', () => {
  it('orders attempts, decisions, shown hints, help and reviews chronologically', () => {
    const entries = buildTimeline(record())
    expect(entries.map(e => e.key)).toEqual(['attempt:attempt_original', 'decision:decision_old', 'hint:hint_synthetic', 'attempt:attempt_correction', 'decision:decision_candidate', 'help:help_synthetic', 'decision:decision_reviewed', 'review:review_synthetic'])
    expect(entries.find(e => e.key === 'attempt:attempt_original')?.title).toBe('原作答')
    const correction = entries.find(e => e.key === 'attempt:attempt_correction')!
    expect(correction.title).toBe('第1次订正'); expect(correction.badges.map(b => b.label)).toEqual(['看过提示', '当前作答']); expect(correction.assetId).toBe('asset_correction')
    expect(entries.find(e => e.kind === 'help')?.badges[0].label).toBe('老师已处理')
    expect(entries.find(e => e.kind === 'help')?.note).toContain('已核对订正图')
    expect(entries.find(e => e.kind === 'review')?.title).toBe('老师确认')
  })
  it('marks stale and retracted results as 已失效 and never current', () => {
    const entries = buildTimeline(record())
    for (const key of ['decision:decision_old', 'decision:decision_candidate']) { const e = entries.find(x => x.key === key)!; expect(e.state).toBe('invalid'); expect(e.badges.map(b => b.label)).toContain('已失效') }
    expect(entries.find(e => e.key === 'decision:decision_candidate')?.title).toBe('AI初判 · 正确')
    const current = entries.filter(e => e.kind === 'decision' && e.state === 'current')
    expect(current.map(e => e.key)).toEqual(['decision:decision_reviewed']); expect(current[0].title).toBe('老师确认 · 正确')
    const replaced = record(); replaced.reviews[0].action = 'replace'
    expect(buildTimeline(replaced).find(e => e.key === 'decision:decision_reviewed')?.title).toBe('老师改判 · 正确')
    expect(entries.find(e => e.kind === 'hint')?.badges.map(b => b.label)).toEqual(['未给出答案', '已失效'])
  })
  it('does not label every unresolved model result as waiting for a teacher', () => {
    const r = record(), decision = r.decisions[0].record
    decision.validity = 'needs_review'; decision.verdict = 'unable_to_judge'; decision.origin = 'model'
    r.case.current_decision = decision
    const entry = buildTimeline(r).find(item => item.key === `decision:${decision.id}`)!
    expect(entry.badges.map(badge => badge.label)).toContain('尚待核实')
    expect(entry.badges.map(badge => badge.label)).not.toContain('待老师核实')
  })
  it('a refresh with currently_effective=false or a cleared case decision cannot revive a result', () => {
    const notEffective = record(); notEffective.decisions[0].currently_effective = false
    expect(buildTimeline(notEffective).some(e => e.kind === 'decision' && e.state === 'current')).toBe(false)
    const cleared = record(); cleared.case.current_decision = null
    expect(buildTimeline(cleared).some(e => e.kind === 'decision' && e.state === 'current')).toBe(false)
    // Even if a lagging read claims "effective", a stale record stays invalid.
    const lagging = record(); lagging.decisions[2].currently_effective = true; lagging.case.current_decision = lagging.decisions[2].record
    expect(buildTimeline(lagging).find(e => e.key === 'decision:decision_old')?.state).toBe('invalid')
    const retired = record(); retired.case.lifecycle = 'superseded'
    expect(buildTimeline(retired).some(e => e.state === 'current')).toBe(false)
  })
  it('numbers corrections by creation and labels help conditions', () => {
    const base = fixture<Attempt>('original_attempt')
    const labels = attemptLabels([{ ...base, id: 'attempt_third', parent_attempt_id: 'attempt_second', created_at: at(30) }, base, { ...base, id: 'attempt_second', parent_attempt_id: base.id, created_at: at(10) }])
    expect([...labels]).toEqual([['attempt_original', '原作答'], ['attempt_second', '第1次订正'], ['attempt_third', '第2次订正']])
    const r = record(); r.attempts[0].record.help_condition = 'answer_shown'; r.attempts[1].record.help_condition = 'staff_help'
    const entries = buildTimeline(r)
    expect(entries.find(e => e.key === 'attempt:attempt_correction')?.badges[0]).toEqual({ label: '看过答案', tone: 'amber' })
    expect(entries.find(e => e.key === 'attempt:attempt_original')?.badges[0].label).toBe('老师帮助')
  })
  it('shows help on the current answer and any still-open request', () => {
    const r = record(); r.help.push({ ...r.help[0], id: 'help_open', attempt_id: 'attempt_original', status: 'waiting', claimed_by: null, claimed_at: null, completed_at: null, resolution_note: null, created_at: at(50) })
    r.help.push({ ...r.help[0], id: 'help_old_closed', attempt_id: 'attempt_original', created_at: at(1) })
    expect(currentHelp(r).map(h => h.id)).toEqual(['help_open', 'help_synthetic'])
  })
})
