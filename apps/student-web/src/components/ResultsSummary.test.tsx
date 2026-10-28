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
function unresolved(reasons: string[]): CaseDetail {
  const value = row('unable_to_judge', 'needs_review')
  value.case.current_decision!.verification = { mode: 'two_model', status: 'degraded', independent_readback: false, reason_codes: reasons }
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

  it.each(['provider_unavailable', 'reconciliation_unavailable'])('keeps %s visible without workflow copy or a settled verdict', reason => {
    const html = render([unresolved([reason])])
    expect(html).toContain('本次作答 1 题 · 正确 0 道 · 错误 0 道')
    expect(html).toContain('<p class="results-breakdown">AI 核对尚未完成 1 道</p>')
    expect(html).not.toMatch(/待确认|笔迹待核对|继续其他题|订正其他题|老师|这些题暂不判对错/)
    expect(html.match(/<p\b/g)).toHaveLength(2)
  })

  it.each(['visual_unreadable', 'answer_disagreement'])('keeps %s pending with the original concise label', reason => {
    const html = render([unresolved([reason])])
    expect(html).toContain('本次作答 1 题 · 正确 0 道 · 错误 0 道')
    expect(html).toContain('<p class="results-breakdown">待确认 1 道</p>')
    expect(html).not.toMatch(/继续其他题|订正其他题|请老师|AI 核对尚未完成|笔迹待核对/)
    expect(html.match(/<p\b/g)).toHaveLength(2)
  })

  it('shows 31 unfinished service checks without calling them handwriting confirmations or settled results', () => {
    const rows = Array.from({ length: 31 }, () => unresolved(['provider_unavailable']))
    const before = structuredClone(rows)
    const html = render(rows)
    expect(html).toContain('本次作答 31 题 · 正确 0 道 · 错误 0 道')
    expect(html).toContain('<p class="results-breakdown">AI 核对尚未完成 31 道</p>')
    expect(html).not.toMatch(/待确认|笔迹待核对|老师|初判|继续其他题|正确率|%/)
    expect(html.match(/<p\b/g)).toHaveLength(2)
    expect(rows).toEqual(before)
  })

  it('counts each mixed unresolved result once while retaining the short header', () => {
    const processing = unresolved(['provider_unavailable']); processing.checkJob = { status: 'running' } as CaseDetail['checkJob']
    const html = render([
      unresolved(['provider_unavailable']), unresolved(['provider_unavailable', 'visual_unreadable']),
      unresolved(['visual_unreadable']), unresolved(['answer_disagreement']), processing, row(), row('incorrect'), row('unanswered'),
    ])
    expect(html).toContain('本次作答 7 题 · 正确 1 道 · 错误 1 道')
    expect(html).toContain('<p class="results-breakdown">未作答 1 道 · AI 核对尚未完成 2 道 · 待确认 2 道 · 处理中 1 道</p>')
    expect(html.match(/<p\b/g)).toHaveLength(2)
  })

  it.each([true, false])('does not turn an unfinished service readback into a confirmed blank (compact: %s)', compact => {
    const value = unresolved(['provider_unavailable'])
    value.attempt.answer_status = 'blank'; value.attempt.answer_text = null
    const html = render([value], compact)
    expect(html).toContain('AI 核对尚未完成 1 道')
    expect(html).not.toContain('未作答')
    if (compact) expect(html).toContain('本次作答 1 题 · 正确 0 道 · 错误 0 道')
    else expect(html).toContain('本次读到 1 题 · AI 初判正确 0 道 · 老师确认正确 0 道 · 需订正 0 道')
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

  it.each(['queued', 'running', 'retryable_failed'] as const)('keeps a %s recheck pending in both summaries', status => {
    const value = row(); value.checkJob = { status } as CaseDetail['checkJob']
    const label = status === 'running' ? '处理中 1 道' : '尚未检查 1 道'
    const compact = render([value]), history = render([value], false)
    expect(compact).toContain('本次作答 1 题 · 正确 0 道 · 错误 0 道')
    expect(history).toContain('本次拍到 1 题：需订正 0，待核实 1，已确认正确 0')
    expect(compact).toContain(label); expect(history).toContain(label)
    expect(history).not.toContain('AI初判正确 1')
  })

  it('does not claim a paused observation is still processing', () => {
    const value = row(); value.checkJob = { status: 'running' } as CaseDetail['checkJob']; value.observationPaused = true
    const html = render([value])
    expect(html).toContain('本次作答 1 题 · 正确 0 道 · 错误 0 道')
    expect(html).toContain('尚未检查 1 道')
    expect(html).not.toContain('处理中')
  })

  it('preserves partial-collection and retired-case context only for noncompact callers', () => {
    const value = row('correct', 'confirmed'); value.case.current_decision!.origin = 'staff'
    const detail: HomeworkDetail = { homework: fixture('homework'), cases: [value], jobs: [], retired: [{ id: 'old_case', lifecycle: 'superseded', replacementCaseIds: [value.case.id] }] }
    const html = renderToStaticMarkup(<ResultsSummary rows={[value]} detail={detail} />)
    expect(html).toContain('本次拍到 1 题：需订正 0，待核实 0，已确认正确 1')
    expect(html).toContain('不代表整份作业的完成情况')
    expect(html).toContain('老师已重新切分此题')
    expect(html).toContain('不能计算整页正确率')
    expect(html).not.toContain('%')
    const compact = renderToStaticMarkup(<ResultsSummary rows={[value]} detail={detail} compact />)
    expect(compact).toContain('本次作答 1 题 · 正确 1 道 · 错误 0 道')
    expect(compact.match(/<p\b/g)).toHaveLength(1)
    expect(compact).not.toMatch(/老师|整份作业|正确率/)
  })

  it('retains separate historical service and handwriting explanations for noncompact callers', () => {
    const confirmed = row('correct', 'confirmed'); confirmed.case.current_decision!.origin = 'staff'
    const html = render([unresolved(['provider_unavailable']), unresolved(['visual_unreadable']), row(), confirmed, row('incorrect')], false)
    expect(html).toContain('本次读到 5 题 · AI 初判正确 1 道 · 老师确认正确 1 道 · 需订正 1 道')
    expect(html).toContain('AI 核对尚未完成 1 道 · 笔迹待核对 1 道')
    expect(html).toContain('这些题暂不判对错')
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
