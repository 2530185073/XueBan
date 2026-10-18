import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import examples from '../../../../contracts/phase1/examples.json'
import type { CaseDetail, Decision, Hint } from '../core/types'
import type { Workspace } from '../useStudentWorkspace'
import { QuestionDetail } from './QuestionDetail'
import { ResultsSummary } from './ResultsSummary'

const fixture = <T,>(name: string) => structuredClone(examples.entries.find(x => x.name === name)!.value) as T
function sample(validity: Decision['validity'], verdict: Decision['verdict']): CaseDetail {
  const row: CaseDetail = { case: fixture('resolved_case'), question: fixture('question_revision_2'), attempt: fixture('correction_across_question_revision') }
  row.case.current_decision = { ...fixture<Decision>('model_candidate'), validity, verdict }
  row.case.progress = 'pending'
  return row
}
const workspace = { busy: '', hint: null, client: {}, recordVersion: 0 } as unknown as Workspace
const button = (html: string, label: string) => html.match(/<button\b[\s\S]*?<\/button>/g)?.find(tag => tag.includes(label))

describe('whole-page correction experience', () => {
  it.each(['correct', 'unable_to_judge'] as const)('keeps an uncertain %s result open for feedback and navigation, without giving solution hints', verdict => {
    const row = sample('needs_review', verdict)
    const hint: Hint = { ...fixture<Hint>('shown_but_invalidated_hint'), validity: 'active', attempt_id: row.attempt.id, source_decision_id: row.case.current_decision!.id, source_decision_revision: row.case.current_decision!.revision, content: '这条未核实的解题提示不应展示' }
    const html = renderToStaticMarkup(<QuestionDetail row={row} workspace={{ ...workspace, hint }} onCorrection={() => {}} onContinue={() => {}} />)
    expect(html).toContain('这道题暂不判对错'); expect(html).toContain('先核实题目和作答')
    expect(html).not.toContain('初判正确'); expect(html).not.toContain(hint.content)
    for (const label of ['先看其他题', '文字和照片不一样', '我已在纸上订正']) {
      expect(button(html, label)).toBeDefined(); expect(button(html, label)).not.toContain('disabled')
    }
    expect(button(html, '给我一点方向')).toContain('disabled'); expect(button(html, '看看关键一步')).toContain('disabled')
  })
  it('offers normal correction hints on a candidate error without claiming staff confirmation', () => {
    const html = renderToStaticMarkup(<QuestionDetail row={sample('candidate', 'incorrect')} workspace={workspace} onCorrection={() => {}} />)
    expect(html).toContain('需要订正'); expect(html).toContain('AI 发现了')
    expect(html).not.toContain('老师确认'); expect(button(html, '给我一点方向')).not.toContain('disabled')
  })
  it('summarises mixed results separately and leaves correction available before all questions are confirmed', () => {
    const rows = [sample('candidate', 'incorrect'), sample('needs_review', 'correct'), sample('candidate', 'correct')]
    const html = renderToStaticMarkup(<ResultsSummary rows={rows} detail={null} />)
    expect(html).toContain('需订正 1，待核实 1，AI初判正确 1，已确认正确 0')
    expect(html).toContain('可以先订正其他题，不必等整页一起确认')
    expect(html).not.toContain('全部正确')
  })
})
