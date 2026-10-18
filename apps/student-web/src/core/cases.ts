import type { CaseBucket, CaseCounts, CaseDetail, CorrectionCase, Decision, RetiredCase } from './types'
import { compareTimestamps, preciseTimestamp } from './chronology'

const invalid = (d: Decision) => d.validity === 'stale' || d.validity === 'retracted'
export const isRetired = (c: Pick<CorrectionCase, 'lifecycle'>) => c.lifecycle === 'superseded' || c.lifecycle === 'deletion_pending'
/** The case's own current decision, only while it still matches the case's current inputs. */
export function currentCaseDecision(c: CorrectionCase): Decision | null {
  const d = c.current_decision
  if (!d || invalid(d) || isRetired(c)) return null
  const v = d.input_versions
  return v.question_id === c.question_id && v.attempt_id === c.current_attempt_id && v.question_revision === c.current_question_revision ? d : null
}
function bucketOf(d: Decision | null): CaseBucket {
  if (!d || d.validity === 'needs_review' || d.verdict === 'unable_to_judge') return 'pending_review'
  if (d.verdict === 'correct') return d.validity === 'confirmed' && d.origin === 'staff' ? 'confirmed_correct' : 'candidate_correct'
  return 'needs_correction'
}
/** Classification from a bare case read (history list); no recognition details are available here. */
export function bucketOfCase(c: CorrectionCase): CaseBucket { return bucketOf(currentCaseDecision(c)) }
/** Classification for an opened homework, where the attempt recognition revision is known too. */
export function bucketOfRow(row: CaseDetail): CaseBucket {
  if (row.attempt.recognition_status !== 'ready') return 'pending_review'
  const d = currentCaseDecision(row.case)
  return bucketOf(d && d.input_versions.attempt_recognition_revision === row.attempt.recognition_revision ? d : null)
}
export const unresolved = (bucket: CaseBucket) => bucket === 'needs_correction' || bucket === 'pending_review'

/**
 * Active case IDs in homework order. A superseded case is replaced in place by
 * its active replacements; retired cases themselves never appear.
 */
export function orderActiveCases(caseIds: string[], records: Map<string, CorrectionCase>, compare?: (a: CorrectionCase, b: CorrectionCase) => number): string[] {
  const out: string[] = [], seen = new Set<string>()
  const sorted = (ids: string[]) => compare ? [...ids].sort((a, b) => { const left = records.get(a), right = records.get(b); return left && right ? compare(left, right) : a.localeCompare(b) }) : ids
  const visit = (id: string, depth: number) => {
    if (seen.has(id) || depth > 8) return
    const c = records.get(id); if (!c) return
    seen.add(id)
    if (!isRetired(c)) out.push(id)
    else if (c.lifecycle === 'superseded') for (const next of sorted(c.replacement_case_ids)) visit(next, depth + 1)
  }
  if (compare) {
    // Repaired questions inherit their original question's place, even when the
    // server also lists the replacement as a top-level case with a newer date.
    const replacements = new Set([...records.values()].filter(c => c.lifecycle === 'superseded').flatMap(c => c.replacement_case_ids))
    for (const id of sorted(caseIds.filter(id => !replacements.has(id)))) visit(id, 0)
  }
  for (const id of sorted(caseIds)) visit(id, 0)
  return out
}

/** Keep the provider's creation order; backend case IDs are random, not page order. */
export function orderCasesByCreation(caseIds: string[], records: Map<string, CorrectionCase>, rows: CaseDetail[] = []): string[] {
  const questions = new Map(rows.map(row => [row.case.id, row.question]))
  return orderActiveCases(caseIds, records, (a, b) => {
    const caseA = preciseTimestamp(a.created_at), caseB = preciseTimestamp(b.created_at)
    return compareTimestamps(preciseTimestamp(questions.get(a.id)?.created_at) ?? caseA, preciseTimestamp(questions.get(b.id)?.created_at) ?? caseB)
      || compareTimestamps(caseA, caseB) || a.id.localeCompare(b.id)
  })
}
export function retiredCases(records: Map<string, CorrectionCase>): RetiredCase[] {
  return [...records.values()].filter(isRetired).map(c => ({ id: c.id, lifecycle: c.lifecycle as RetiredCase['lifecycle'], replacementCaseIds: [...c.replacement_case_ids] }))
}
export function countBuckets(buckets: CaseBucket[]): CaseCounts {
  const counts: CaseCounts = { total: buckets.length, needs_correction: 0, pending_review: 0, candidate_correct: 0, confirmed_correct: 0 }
  for (const bucket of buckets) counts[bucket]++
  return counts
}
/** First case needing the student's correction, else the first still awaiting verification. */
export function firstUnresolved<T>(items: T[], bucket: (item: T) => CaseBucket): T | undefined {
  return items.find(item => bucket(item) === 'needs_correction') ?? items.find(item => bucket(item) === 'pending_review')
}
