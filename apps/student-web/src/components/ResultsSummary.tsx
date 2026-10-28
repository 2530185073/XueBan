import { bucketOfRow, countBuckets } from '../core'
import type { CaseDetail, HomeworkDetail } from '../core/types'
import { countsSentence, coverageNote } from '../history'
import { studentResultState, studentUnresolvedKind } from '../presentation'

/** Per-photo counts for an opened homework; partial collection never becomes an accuracy figure. */
export function ResultsSummary({ rows, detail, compact = false }: { rows: CaseDetail[]; detail: HomeworkDetail | null; compact?: boolean }) {
  const results = rows.map(row => {
    const unresolvedKind = studentUnresolvedKind(row)
    // An unfinished service readback cannot establish that the answer was blank.
    const state = unresolvedKind === 'service' ? 'uncertain' : studentResultState(row)
    return { row, state, unresolvedKind }
  })
  const correct = results.filter(result => result.state === 'correct').length
  const incorrect = results.filter(result => result.state === 'incorrect').length
  const unanswered = results.filter(result => result.state === 'unanswered').length
  const uncertainRows = results.filter(result => result.state === 'uncertain')
  const serviceIncomplete = uncertainRows.filter(result => result.unresolvedKind === 'service').length
  const handwriting = uncertainRows.filter(result => result.unresolvedKind === 'handwriting').length
  const uncertain = uncertainRows.length - serviceIncomplete - (compact ? 0 : handwriting)
  const processing = results.filter(result => result.state === 'processing').length
  const unprocessed = results.filter(result => result.state === 'unprocessed').length
  const serviceFailure = results.filter(result => result.state === 'service_failure').length
  const breakdown = [
    unanswered > 0 ? `未作答 ${unanswered} 道` : null,
    serviceIncomplete > 0 ? `AI 核对尚未完成 ${serviceIncomplete} 道` : null,
    !compact && handwriting > 0 ? `笔迹待核对 ${handwriting} 道` : null,
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
  const counts = {
    ...countBuckets(results.filter(result => result.state !== 'unanswered').map(({ row, state }) => state === 'correct' || state === 'incorrect' ? bucketOfRow(row) : 'pending_review')),
    total: rows.length,
  }
  const resultCounts = `本次读到 ${rows.length} 题 · AI 初判正确 ${counts.candidate_correct} 道 · 老师确认正确 ${counts.confirmed_correct} 道 · 需订正 ${incorrect} 道`
  const serviceNote = '核对服务尚未完成，这些题暂不判对错。可以先看原图，或继续其他题。'
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
