import { bucketOfRow, countBuckets } from '../core'
import type { CaseDetail, HomeworkDetail } from '../core/types'
import { countsSentence, coverageNote } from '../history'
import { studentResultState } from '../presentation'

/** Per-photo counts for an opened homework; partial collection never becomes an accuracy figure. */
export function ResultsSummary({ rows, detail, compact = false }: { rows: CaseDetail[]; detail: HomeworkDetail | null; compact?: boolean }) {
  const correct = rows.filter(row => studentResultState(row) === 'correct').length
  const incorrect = rows.filter(row => studentResultState(row) === 'incorrect').length
  const unanswered = rows.filter(row => studentResultState(row) === 'unanswered').length
  const uncertain = rows.filter(row => studentResultState(row) === 'uncertain').length
  const processing = rows.filter(row => studentResultState(row) === 'processing').length
  const unprocessed = rows.filter(row => studentResultState(row) === 'unprocessed').length
  const serviceFailure = rows.filter(row => studentResultState(row) === 'service_failure').length
  const breakdown = [
    unanswered > 0 ? `未作答 ${unanswered} 道` : null,
    uncertain > 0 ? `待确认 ${uncertain} 道` : null,
    processing > 0 ? `处理中 ${processing} 道` : null,
    unprocessed > 0 ? `尚未检查 ${unprocessed} 道` : null,
    serviceFailure > 0 ? `服务失败 ${serviceFailure} 道` : null,
  ].filter(Boolean).join(' · ')
  if (compact) {
    return <div className="results-summary compact-results-summary" role="status">
      <p className="results-counts">本次作答 {rows.length - unanswered} 题 · 正确 {correct} 道 · 错误 {incorrect} 道</p>
      {breakdown && <p className="results-breakdown">{breakdown}</p>}
    </div>
  }
  const counts = { ...countBuckets(rows.filter(row => studentResultState(row) !== 'unanswered').map(row => studentResultState(row) === 'processing' ? 'pending_review' : bucketOfRow(row))), total: rows.length }
  const note = detail ? coverageNote(detail.homework.coverage) : null
  const repaired = (detail?.retired ?? []).filter(c => c.lifecycle === 'superseded').length
  return <div className="results-summary" role="status">
    <p className="results-counts">{countsSentence(counts)}</p>
    {breakdown && <p className="results-breakdown">{breakdown}</p>}
    {note && <p className="muted small">{note}</p>}
    {repaired > 0 && <p className="muted small retired-note">老师已重新切分此题{repaired > 1 ? `（共 ${repaired} 处）` : ''}，原来的识别条目不再显示，下面是重新切分后的题目。</p>}
  </div>
}
