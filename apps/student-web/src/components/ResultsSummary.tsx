import { bucketOfRow, countBuckets } from '../core'
import type { CaseDetail, HomeworkDetail } from '../core/types'
import { countsSentence, coverageNote } from '../history'
import { studentResultState } from '../presentation'

/** Per-photo counts for an opened homework; partial collection never becomes an accuracy figure. */
export function ResultsSummary({ rows, detail, compact = false }: { rows: CaseDetail[]; detail: HomeworkDetail | null; compact?: boolean }) {
  const counts = countBuckets(rows.map(row => studentResultState(row) === 'processing' ? 'pending_review' : bucketOfRow(row)))
  const incorrect = rows.filter(row => studentResultState(row) === 'incorrect').length
  const uncertain = rows.filter(row => studentResultState(row) === 'uncertain').length
  const processing = rows.filter(row => studentResultState(row) === 'processing').length
  const unprocessed = rows.filter(row => studentResultState(row) === 'unprocessed').length
  const serviceFailure = rows.filter(row => studentResultState(row) === 'service_failure').length
  const breakdown = [
    uncertain > 0 ? `待确认 ${uncertain} 道` : null,
    processing > 0 ? `处理中 ${processing} 道` : null,
    unprocessed > 0 ? `尚未检查 ${unprocessed} 道` : null,
    serviceFailure > 0 ? `服务失败 ${serviceFailure} 道` : null,
  ].filter(Boolean).join(' · ')
  if (compact) {
    return <div className="results-summary compact-results-summary" role="status">
      <p className="results-counts">本次读到 {rows.length} 题 · AI 初判正确 {counts.candidate_correct} 道 · 老师确认正确 {counts.confirmed_correct} 道 · 需订正 {incorrect} 道</p>
      {breakdown && <p className="results-breakdown">{breakdown}</p>}
    </div>
  }
  const note = detail ? coverageNote(detail.homework.coverage) : null
  const repaired = (detail?.retired ?? []).filter(c => c.lifecycle === 'superseded').length
  return <div className="results-summary" role="status">
    <p className="results-counts">{countsSentence(counts)}</p>
    {breakdown && <p className="results-breakdown">{breakdown}</p>}
    {counts.pending_review > 0 && <p className="muted small">待核实的题暂不判对错，可以先订正其他题，不必等整页一起确认。</p>}
    {note && <p className="muted small">{note}</p>}
    {repaired > 0 && <p className="muted small retired-note">老师已重新切分此题{repaired > 1 ? `（共 ${repaired} 处）` : ''}，原来的识别条目不再显示，下面是重新切分后的题目。</p>}
  </div>
}
