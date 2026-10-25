import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { isReportSnapshot, type ReportSnapshot } from '../api/learningTypes'
import { DailySnapshotView } from './DailyFeedbackArea'
import { SnapshotView } from './ReportDetail'

const snapshot: ReportSnapshot = {
  period: { start: '2026-10-26', end_exclusive: '2026-10-27' },
  coverage: { observed_days: 1, observed_dates: ['2026-10-26'], homeworks: 1, partial_period: false, first_seen_date: '2026-10-26' },
  school_tasks: { questions: 2, staff_verified: 2, awaiting_verification: 0, not_judged: 0,
    verdicts: { correct: 2, incorrect: 0, partially_correct: 0, unanswered: 0 }, corrected_resolved: 0, correction_open: 0 },
  help: { requests: 0, by_kind: {}, resolved: 0, hints_viewed: 0, answers_shown: 1, staff_help: [] },
  focus: [], steady: [], unknown_current: [], sufficient: false, sections: {}, limitations: '仅按实际证据统计。',
  completions: [{ case_id: 'case_1', homework_title: '方程', page: null, question: '2x + 3 = 11', verdict: null,
    progress: 'verified_resolved', correction: '补做完成，老师确认通过', source_role: 'sitting_teacher', certainty: '老师已核实' }],
}

describe('completion and solution exposure report semantics', () => {
  it('accepts new completion data while remaining compatible with old snapshots', () => {
    expect(isReportSnapshot(snapshot)).toBe(true)
    expect(isReportSnapshot({ ...snapshot, completions: undefined })).toBe(true)
    expect(isReportSnapshot({ ...snapshot, completions: [{ ...snapshot.completions![0], progress: null }] })).toBe(false)
  })
  it.each([DailySnapshotView, SnapshotView])('shows blank follow-up separately and standalone solution views', View => {
    const html = renderToStaticMarkup(createElement(View, { snap: snapshot }))
    expect(html).toContain('待完成与补做')
    expect(html).toContain('补做完成，老师确认通过')
    expect(html).toContain('查看答案或解法 1 道')
    expect(html).not.toContain('今天错的题和订正情况')
    expect(html).not.toContain('其中含答案')
  })
})
