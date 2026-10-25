import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import examples from '../../../../contracts/phase1/examples.json'
import type { CaseDetail, Decision, HomeworkDetail } from '../core/types'
import { ResultsSummary } from './ResultsSummary'

const fixture = <T,>(name: string) => structuredClone(examples.entries.find(entry => entry.name === name)!.value) as T
function row(verdict: Decision['verdict'] = 'correct', validity: Decision['validity'] = 'candidate'): CaseDetail {
  const value: CaseDetail = { case: fixture('resolved_case'), question: fixture('question_revision_2'), attempt: fixture('correction_across_question_revision') }
  value.case.current_decision = { ...fixture<Decision>('model_candidate'), verdict, validity }
  return value
}
const render = (rows: CaseDetail[], compact = true) => renderToStaticMarkup(<ResultsSummary rows={rows} detail={null} compact={compact} />)

describe('original concise results summary', () => {
  it('shows only the original total, correct and incorrect counts for settled results', () => {
    const confirmed = row('correct', 'confirmed'); confirmed.case.current_decision!.origin = 'staff'
    const html = render([row(), confirmed, row('incorrect'), row('partially_correct')])
    expect(html).toContain('<p class="results-counts">本次作答 4 题 · 正确 2 道 · 错误 2 道</p>')
    expect(html.match(/<p\b/g)).toHaveLength(1)
    expect(html).not.toMatch(/AI 初判正确|老师确认正确|需订正|正确率|%|求助|订正记录/)
  })

  it('keeps unresolved, processing, unprocessed and failed cases out of correct and incorrect counts', () => {
    const uncertain = row('correct', 'needs_review')
    const unable = row('unable_to_judge')
    const processing = row(); processing.checkJob = { status: 'running' } as CaseDetail['checkJob']
    const unprocessed = row(); unprocessed.case.current_decision = null
    const failed = row(); failed.case.current_decision = null; failed.checkJob = { status: 'failed' } as CaseDetail['checkJob']
    const html = render([uncertain, unable, processing, unprocessed, failed])
    expect(html).toContain('本次作答 5 题 · 正确 0 道 · 错误 0 道')
    expect(html).toContain('<p class="results-breakdown">待确认 2 道 · 处理中 1 道 · 尚未检查 1 道 · 服务失败 1 道</p>')
    expect(html.match(/<p\b/g)).toHaveLength(2)
  })

  it.each(['provider_unavailable', 'reconciliation_unavailable', 'visual_unreadable', 'answer_disagreement'])('counts %s as pending without adding workflow copy', reason => {
    const value = row('unable_to_judge', 'needs_review')
    value.case.current_decision!.verification = { mode: 'two_model', status: 'conflict', independent_readback: true, reason_codes: [reason] }
    const html = render([value])
    expect(html).toContain('本次作答 1 题 · 正确 0 道 · 错误 0 道')
    expect(html).toContain('待确认 1 道')
    expect(html).not.toMatch(/继续其他题|订正其他题|请老师|AI 核对尚未完成|笔迹待核对/)
    expect(html.match(/<p\b/g)).toHaveLength(2)
  })

  it.each(['stale', 'retracted', 'recognition_changed', 'question_changed', 'attempt_changed'] as const)('does not count an obsolete %s verdict', reason => {
    const value = row()
    if (reason === 'stale' || reason === 'retracted') value.case.current_decision!.validity = reason
    else if (reason === 'recognition_changed') value.attempt.recognition_revision += 1
    else if (reason === 'question_changed') value.case.current_question_revision += 1
    else value.case.current_attempt_id = 'another_attempt'
    const html = render([value])
    expect(html).toContain('本次作答 1 题 · 正确 0 道 · 错误 0 道')
    expect(html).toContain('尚未检查 1 道')
  })

  it('keeps valid starred correct verdicts in the correct count without inventing a failure', () => {
    const value = row(); value.question.recognition_uncertainties = ['数字有一点模糊']
    expect(render([value])).toContain('本次作答 1 题 · 正确 1 道 · 错误 0 道')
  })

  it('keeps a running recheck pending even in the noncompact summary', () => {
    const value = row(); value.checkJob = { status: 'running' } as CaseDetail['checkJob']
    const html = render([value], false)
    expect(html).toContain('本次拍到 1 题：需订正 0，待核实 1，已确认正确 0')
    expect(html).toContain('处理中 1 道')
    expect(html).not.toContain('AI初判正确 1')
  })

  it('preserves partial-collection and retired-case context for noncompact callers without an accuracy claim', () => {
    const value = row('correct', 'confirmed'); value.case.current_decision!.origin = 'staff'
    const detail: HomeworkDetail = { homework: fixture('homework'), cases: [value], jobs: [], retired: [{ id: 'old_case', lifecycle: 'superseded', replacementCaseIds: [value.case.id] }] }
    const html = renderToStaticMarkup(<ResultsSummary rows={[value]} detail={detail} />)
    expect(html).toContain('本次拍到 1 题：需订正 0，待核实 0，已确认正确 1')
    expect(html).toContain('不代表整份作业的完成情况')
    expect(html).toContain('老师已重新切分此题')
    expect(html).toContain('不能计算整页正确率')
    expect(html).not.toContain('%')
  })

  it('does not claim all answers are correct for an empty result', () => {
    expect(render([])).toContain('本次作答 0 题 · 正确 0 道 · 错误 0 道')
    expect(render([])).not.toMatch(/全部正确|100%/)
  })

  it.each([true, false])('counts blanks separately without adding them to incorrect or pending counts (compact: %s)', compact => {
    const blank = row('unable_to_judge', 'needs_review')
    blank.attempt.answer_status = 'blank'; blank.attempt.answer_text = null
    const html = render([row(), row('incorrect'), blank, row('unanswered')], compact)
    expect(html).toContain('<p class="results-breakdown">未作答 2 道</p>')
    expect(html).not.toMatch(/待确认|待核实 [1-9]/)
    if (compact) expect(html).toContain('本次作答 2 题 · 正确 1 道 · 错误 1 道')
    else expect(html).toContain('本次拍到 4 题：需订正 1，待核实 0')
  })
})
