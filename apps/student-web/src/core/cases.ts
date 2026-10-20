import type { CaseBucket, CaseCounts, CaseDetail, CorrectionCase, Decision, RetiredCase } from './types'

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
  const d = currentCaseDecision(row.case)
  if (!d || d.input_versions.attempt_recognition_revision !== row.attempt.recognition_revision) return 'pending_review'
  return bucketOf(d)
}
export const unresolved = (bucket: CaseBucket) => bucket === 'needs_correction' || bucket === 'pending_review'

/**
 * Active case IDs in homework order. A superseded case is replaced in place by
 * its active replacements; retired cases themselves never appear.
 */
export function orderActiveCases(caseIds: string[], records: Map<string, CorrectionCase>): string[] {
  const out: string[] = [], seen = new Set<string>()
  const visit = (id: string, depth: number) => {
    if (seen.has(id) || depth > 8) return
    const c = records.get(id); if (!c) return
    seen.add(id)
    if (!isRetired(c)) out.push(id)
    else if (c.lifecycle === 'superseded') for (const next of c.replacement_case_ids) visit(next, depth + 1)
  }
  for (const id of caseIds) visit(id, 0)
  return out
}

/**
 * The server's case IDs are storage identifiers, so their lexical order is
 * unrelated to where a question appears in the photographed page. Sort the
 * fully read rows by the first question region after the original pages have
 * been put in page order.
 */
export function orderRowsByPhoto(rows: CaseDetail[], assetIds: string[]): CaseDetail[] {
  const pageOrder = new Map(assetIds.map((id, index) => [id, index]))
  const positioned = rows.map((row, originalIndex) => {
    const region = [...row.question.regions].sort((a, b) => {
      const pageA = pageOrder.get(a.asset_id) ?? Number.MAX_SAFE_INTEGER
      const pageB = pageOrder.get(b.asset_id) ?? Number.MAX_SAFE_INTEGER
      return pageA - pageB || a.y - b.y || a.x - b.x
    })[0]
    return {
      row,
      originalIndex,
      region,
      position: region
        ? [pageOrder.get(region.asset_id) ?? Number.MAX_SAFE_INTEGER, region.y, region.x]
        : [Number.MAX_SAFE_INTEGER, Number.MAX_SAFE_INTEGER, Number.MAX_SAFE_INTEGER],
    }
  }).sort((a, b) => a.position[0] - b.position[0] || a.position[1] - b.position[1] || a.position[2] - b.position[2] || a.originalIndex - b.originalIndex)
  // Anchor each row at its first box so a tilted two-column page stays in
  // reading order without a non-transitive fuzzy sort comparator.
  const lines: (typeof positioned)[] = []
  for (const item of positioned) {
    const box = item.region
    const line = box && [...lines].reverse().find(line => {
      const anchor = line[0]
      return anchor.region && item.position[0] === anchor.position[0]
        && Math.abs(box.y - anchor.region.y) <= Math.min(box.height, anchor.region.height) * 0.15
        && line.every(other => other.region && (box.x >= other.region.x + other.region.width * 0.8 || other.region.x >= box.x + box.width * 0.8))
    })
    if (line) line.push(item)
    else lines.push([item])
  }
  return lines.flatMap(line => line.sort((a, b) => a.position[2] - b.position[2] || a.originalIndex - b.originalIndex).map(item => item.row))
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
