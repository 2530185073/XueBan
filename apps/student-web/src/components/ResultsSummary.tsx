import { bucketOfRow, countBuckets } from '../core'
import type { CaseDetail, HomeworkDetail } from '../core/types'
import { countsSentence, coverageNote } from '../history'
import { studentResultState, studentUnresolvedKind } from '../presentation'

/** Per-photo counts for an opened homework; partial collection never becomes an accuracy figure. */
export function ResultsSummary({ rows, detail, compact = false }: { rows: CaseDetail[]; detail: HomeworkDetail | null; compact?: boolean }) {
  const counts = countBuckets(rows.map(row => studentResultState(row) === 'processing' ? 'pending_review' : bucketOfRow(row)))
  const incorrect = rows.filter(row => studentResultState(row) === 'incorrect').length
  const uncertainRows = rows.filter(row => studentResultState(row) === 'uncertain')
  const serviceIncomplete = uncertainRows.filter(row => studentUnresolvedKind(row) === 'service').length
  const handwriting = uncertainRows.filter(row => studentUnresolvedKind(row) === 'handwriting').length
  const uncertain = uncertainRows.length - serviceIncomplete - handwriting
  const processing = rows.filter(row => studentResultState(row) === 'processing').length
  const unprocessed = rows.filter(row => studentResultState(row) === 'unprocessed').length
  const serviceFailure = rows.filter(row => studentResultState(row) === 'service_failure').length
  const breakdown = [
    serviceIncomplete > 0 ? `AI 核对尚未完成 ${serviceIncomplete} 道` : null,
    handwriting > 0 ? `笔迹待核对 ${handwriting} 道` : null,
    uncertain > 0 ? `待确认 ${uncertain} 道` : null,
    processing > 0 ? `处理中 ${processing} 道` : null,
    unprocessed > 0 ? `尚未检查 ${unprocessed} 道` : null,
    serviceFailure > 0 ? `服务失败 ${serviceFailure} 道` : null,
  ].filter(Boolean).join(' · ')
  const resultCounts = `本次读到 ${rows.length} 题 · AI 初判正确 ${counts.candidate_correct} 道 · 老师确认正确 ${counts.confirmed_correct} 道 · 需订正 ${incorrect} 道`
  const serviceNote = '核对服务尚未完成，这些题暂不判对错。可以先看原图，或继续其他题。'
  if (compact) {
    return <div className="results-summary compact-results-summary" role="status">
      <p className="results-counts">{resultCounts}</p>
      {breakdown && <p className="results-breakdown">{breakdown}</p>}
      {serviceIncomplete > 0 && <p className="muted small">{serviceNote}</p>}
    </div>
  }
  const note = detail ? coverageNote(detail.homework.coverage) : null
  const repaired = (detail?.retired ?? []).filter(c => c.lifecycle === 'superseded').length
  return <div className="results-summary" role="status">
    <p className="results-counts">{serviceIncomplete > 0 ? resultCounts : countsSentence(counts)}</p>
    {breakdown && <p className="results-breakdown">{breakdown}</p>}
    {counts.pending_review > 0 && <p className="muted small">{serviceIncomplete > 0 ? serviceNote : '待核实的题暂不判对错，可以先订正其他题，不必等整页一起确认。'}</p>}
    {note && <p className="muted small">{note}</p>}
    {repaired > 0 && <p className="muted small retired-note">老师已重新切分此题{repaired > 1 ? `（共 ${repaired} 处）` : ''}，原来的识别条目不再显示，下面是重新切分后的题目。</p>}
  </div>
}
