import { describe, expect, it } from 'vitest'
import examples from '../../../../contracts/phase1/examples.json'
import { orderCasesByCreation } from './cases'
import type { CaseDetail, CorrectionCase } from './types'

const base = examples.entries.find(entry => entry.name === 'resolved_case')!.value as CorrectionCase
const makeCase = (id: string, created_at: string) => ({ ...structuredClone(base), id, created_at })

describe('stable question order fallbacks', () => {
  it('prefers question creation dates and falls back to case creation when unavailable', () => {
    const first = makeCase('case_z', '2026-10-18T00:00:00Z'), next = makeCase('case_a', '2026-10-17T00:00:00Z')
    const records = new Map([first, next].map(c => [c.id, c]))
    const rows = [{ case: next, question: { created_at: '2026-10-19T00:00:00Z' } }, { case: first, question: {} }] as CaseDetail[]
    expect(orderCasesByCreation([next.id, first.id], records, rows)).toEqual([first.id, next.id])
  })
  it('uses a deterministic ID fallback for equal or absent timestamps regardless of incoming order', () => {
    for (const createdAt of ['2026-10-18T00:00:00Z', '']) {
      const a = makeCase('case_a', createdAt), z = makeCase('case_z', createdAt), records = new Map([a, z].map(c => [c.id, c]))
      expect(orderCasesByCreation([z.id, a.id], records)).toEqual([a.id, z.id])
      expect(orderCasesByCreation([a.id, z.id], records)).toEqual([a.id, z.id])
    }
  })
  it('compares timezone-equivalent dates by their remaining sub-millisecond precision', () => {
    const first = makeCase('case_z', '2026-10-18T08:00:00.000001+08:00'), next = makeCase('case_a', '2026-10-18T00:00:00.000002Z')
    expect(orderCasesByCreation([next.id, first.id], new Map([first, next].map(c => [c.id, c])))).toEqual([first.id, next.id])
  })
})
