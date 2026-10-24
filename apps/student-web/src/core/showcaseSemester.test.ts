import fs from 'node:fs'
import crypto from 'node:crypto'
import { describe, expect, it } from 'vitest'
import semester from './showcaseSemester.json'
import type { KnowledgeDetail, KnowledgeMap } from './learning'

const map = semester.map as KnowledgeMap
const details = semester.details as Record<string, KnowledgeDetail>
const nodes = map.nodes ?? []
const evidence = Object.values(details).flatMap(detail => detail.evidence)
const DAY = 86400000

describe('complete PEP grade-seven semester showcase', () => {
  it('contains the full six-chapter curriculum and every node has evidence', () => {
    expect(nodes).toHaveLength(75)
    expect(new Set(nodes.map(node => node.chapter_id))).toHaveLength(6)
    expect(semester.meta.relationCount).toBe(71)
    expect(map.edges).toHaveLength(54)
    for (const node of nodes) {
      const detail = details[node.id]
      expect(detail, node.id).toBeDefined()
      expect(detail.evidence.length, node.id).toBeGreaterThan(0)
      expect(detail.evidence.length, node.id).toBe(node.valid_evidence_count)
      expect(detail.state?.learning_status, node.id).toBe(node.learning_status)
      expect(detail.evidence.every(item => item.question_text && item.reference_answer && item.answer_text), node.id).toBe(true)
      const fact = semester.facts[node.id as keyof typeof semester.facts]
      expect(fact, node.id).toBeDefined()
      expect(fact.possible_error_patterns.length, node.id).toBeGreaterThan(0)
      expect(fact.reviewed_question_count, node.id).toBeGreaterThan(0)
      expect(fact.correction_count, node.id).toBe(detail.evidence.filter(item => item.event_type === 'correction').length)
      expect(fact.introduced_at <= fact.last_record_at, node.id).toBe(true)
    }
  })
  it('keeps every record within one semester and matches the audited source bytes', () => {
    expect(evidence).toHaveLength(209)
    for (const item of evidence) {
      expect(item.occurred_at >= '2025-08-31T16:00:00.000Z').toBe(true)
      expect(item.occurred_at <= '2026-01-29T15:59:59.999Z').toBe(true)
      expect(item.answer_text).not.toContain('undefined')
    }
    const source = fs.readFileSync(new URL('../../../../services/backend/xueban_backend/data/curriculum/candidates/pep-math-g7-up-9787107382468-candidate-r3-20261021.json', import.meta.url), 'utf8')
    expect(crypto.createHash('sha256').update(source).digest('hex')).toBe(semester.meta.sourceSha256)
  })
  it('uses real parent links for all 30 corrections', () => {
    const corrections = evidence.filter(item => item.event_type === 'correction')
    expect(corrections).toHaveLength(30)
    for (const correction of corrections) {
      const siblings = Object.values(details).flatMap(detail => detail.evidence)
      const parent = siblings.find(item => item.attempt_id === correction.parent_attempt_id)
      expect(parent, correction.id).toBeDefined()
      expect(parent?.result).toBe('incorrect')
      expect(parent?.attempt_id).not.toBe(correction.attempt_id)
      expect(parent?.occurred_at! < correction.occurred_at).toBe(true)
    }
  })
  it('derives stable states only from independent successes across dates and families', () => {
    for (const node of nodes.filter(item => item.learning_status === 'stable_for_now')) {
      const wins = details[node.id].evidence.filter(item => item.independent && item.result === 'correct')
      expect(new Set(wins.map(item => item.occurred_at.slice(0, 10))).size, node.id).toBeGreaterThanOrEqual(2)
      expect(new Set(wins.map(item => item.item_family_id)).size, node.id).toBeGreaterThanOrEqual(2)
      expect(new Date(wins.at(-1)!.occurred_at).getTime() - new Date(wins[0].occurred_at).getTime(), node.id).toBeGreaterThanOrEqual(DAY)
    }
    expect(semester.meta.statusCounts).toEqual({ stable_for_now: 37, consolidating: 21, needs_help_now: 9, no_judgment: 8 })
  })
})
