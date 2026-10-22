import type { CaseDetail, HomeworkDetail, HomeworkDetailRead } from './types'
import { StudentError } from './errors'
import { valid } from './validation'
import { isRetired, orderActiveCases, orderCasesByCreation, orderRowsByPhoto, retiredCases } from './cases'
import { compareCreated } from './chronology'

export type { HomeworkDetailRead } from './types'
const invalid = (): never => { throw new StudentError('invalid_response', 502) }
const stale = (): never => { throw new StudentError('stale_detail', 409) }
function uniqueMap<T>(items: T[], key: (item: T) => string): Map<string, T> {
  const map = new Map<string, T>()
  for (const item of items) { const id = key(item); if (map.has(id)) invalid(); map.set(id, item) }
  return map
}
function exactIds<T>(ids: string[], records: Map<string, T>) {
  if (new Set(ids).size !== ids.length || ids.length !== records.size || ids.some(id => !records.has(id))) invalid()
}
function references(ids: string[], allowed: Set<string>) {
  if (new Set(ids).size !== ids.length || ids.some(id => !allowed.has(id))) invalid()
}

/** Validate the complete bundle before returning any row or touching local storage. */
export function detailFromSnapshot(value: unknown, homeworkId: string, studentId: string): HomeworkDetail {
  if (!valid('HomeworkDetailRead', value)) invalid()
  // The returned detail never shares mutable objects with a caller's response.
  const snapshot = structuredClone(value) as HomeworkDetailRead
  const { homework, case_records, questions, attempts, checks, jobs } = snapshot
  if (homework.id !== homeworkId || homework.scope.student_id !== studentId) throw new StudentError('access_denied', 403)
  if (homework.processing_blocked) throw new StudentError('processing_blocked', 409)
  const allRecords = uniqueMap(case_records, c => c.id)
  exactIds(homework.case_ids, allRecords)
  const records = new Map(homework.case_ids.map(id => [id, allRecords.get(id)!]))
  const questionIds = new Set(homework.question_ids), assetIds = new Set(homework.asset_ids)
  if (questionIds.size !== homework.question_ids.length || assetIds.size !== homework.asset_ids.length) invalid()
  for (const c of records.values()) {
    if (c.homework_id !== homework.id || c.scope.student_id !== homework.scope.student_id || c.scope.tenant_id !== homework.scope.tenant_id || c.scope.campus_id !== homework.scope.campus_id) throw new StudentError('access_denied', 403)
    if (!questionIds.has(c.question_id) || !c.attempt_ids.includes(c.current_attempt_id) || !c.attempt_ids.includes(c.root_attempt_id)) invalid()
    references(c.replacement_case_ids, new Set(records.keys()))
    const decision = c.current_decision
    if (decision && (decision.case_id !== c.id || decision.input_versions.question_id !== c.question_id || !c.attempt_ids.includes(decision.input_versions.attempt_id))) invalid()
  }
  // A malformed replacement graph must not silently turn a missing row into a
  // smaller successful homework. Shared replacements (merges) are still valid.
  const visiting = new Set<string>(), depths = new Map<string, number>()
  const depth = (id: string): number => {
    if (visiting.has(id)) invalid()
    const known = depths.get(id); if (known !== undefined) return known
    visiting.add(id)
    const c = records.get(id)!
    const longest = c.lifecycle === 'superseded' && c.replacement_case_ids.length
      ? 1 + Math.max(...c.replacement_case_ids.map(depth)) : 0
    visiting.delete(id)
    if (longest > 8) invalid()
    depths.set(id, longest)
    return longest
  }
  for (const id of homework.case_ids) depth(id)

  const activeIds = orderActiveCases(homework.case_ids, records)
  const activeCases = activeIds.map(id => records.get(id)!)
  if (activeCases.some(isRetired)) invalid()
  const questionMap = uniqueMap(questions, q => q.record.question_id)
  const attemptMap = uniqueMap(attempts, a => a.record.id)
  const checkMap = uniqueMap(checks, check => check.id)
  const jobMap = uniqueMap(jobs, job => job.id)
  exactIds([...new Set(activeCases.map(c => c.question_id))], questionMap)
  exactIds(activeCases.map(c => c.current_attempt_id), attemptMap)
  exactIds(activeCases.flatMap(c => c.check_ids), checkMap)
  exactIds(homework.job_ids, jobMap)

  const rows = activeCases.map(c => {
    const q = questionMap.get(c.question_id)!, a = attemptMap.get(c.current_attempt_id)!
    if (q.record.homework_id !== homework.id || a.record.case_id !== c.id || a.record.question_id !== c.question_id) throw new StudentError('access_denied', 403)
    if (!q.currently_effective || !a.currently_effective || q.record.lifecycle !== 'active' || q.current_revision !== q.record.revision || q.record.revision !== c.current_question_revision || a.current_revision !== a.record.revision || a.record.root_attempt_id !== c.root_attempt_id) stale()
    references(a.record.asset_ids, assetIds)
    if (q.record.regions.some(region => !assetIds.has(region.asset_id)) || a.record.answer_regions.some(region => !assetIds.has(region.asset_id))) invalid()
    const row: CaseDetail = { case: c, question: q.record, attempt: a.record }
    // Keep older checks in the bundle, then apply the exact current-input rule
    // used by the original loader. A newer unrelated check is not a current job.
    const rowChecks = c.check_ids.map(id => {
      const check = checkMap.get(id)!
      if (check.case_id !== c.id || check.input_versions.question_id !== c.question_id || !c.attempt_ids.includes(check.input_versions.attempt_id) || !jobMap.has(check.job_id)) invalid()
      return check
    })
    for (const check of rowChecks.sort(compareCreated).reverse()) {
      const v = check.input_versions
      if (v.question_id === c.question_id && v.question_revision === c.current_question_revision && v.attempt_id === c.current_attempt_id && v.attempt_recognition_revision === a.record.recognition_revision) { row.checkJob = jobMap.get(check.job_id)!; break }
    }
    return row
  })
  const rowMap = new Map(rows.map(row => [row.case.id, row]))
  const ordered = orderCasesByCreation(homework.case_ids, records, rows).map(id => rowMap.get(id)!)
  return { homework, cases: orderRowsByPhoto(ordered, homework.asset_ids), jobs: homework.job_ids.map(id => jobMap.get(id)!), retired: retiredCases(records) }
}
