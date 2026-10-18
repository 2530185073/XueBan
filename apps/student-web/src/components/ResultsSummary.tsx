import { bucketOfRow, countBuckets } from '../core'
import type { CaseDetail, HomeworkDetail } from '../core/types'
import { countsSentence, coverageNote } from '../history'
import { studentResultState } from '../presentation'

/** Per-photo counts for an opened homework; partial collection never becomes an accuracy figure. */
export function ResultsSummary({ rows, detail, compact = false }: { rows: CaseDetail[]; detail: HomeworkDetail | null; compact?: boolean }) {
  if (compact) {
    const correct = rows.filter(row => studentResultState(row) === 'correct').length
    const incorrect = rows.filter(row => studentResultState(row) === 'incorrect').length
    return <div className="results-summary compact-results-summary" role="status">
      <p className="results-counts">本次作答 {rows.length} 题 · 正确 {correct} 道 · 错误 {incorrect} 道</p>
    </div>
  }
  const counts = countBuckets(rows.map(bucketOfRow))
  const note = detail ? coverageNote(detail.homework.coverage) : null
  const repaired = (detail?.retired ?? []).filter(c => c.lifecycle === 'superseded').length
  return <div className="results-summary" role="status">
    <p className="results-counts">{countsSentence(counts)}</p>
    {note && <p className="muted small">{note}</p>}
    {repaired > 0 && <p className="muted small retired-note">老师已重新切分此题{repaired > 1 ? `（共 ${repaired} 处）` : ''}，原来的识别条目不再显示，下面是重新切分后的题目。</p>}
  </div>
}
