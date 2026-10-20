import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import type { CaseDetail } from '../core/types'
import { QuestionCard } from './RecognitionResults'

const row = (): CaseDetail => ({
  case: { id: 'case_test', lifecycle: 'active', question_id: 'question_test', current_attempt_id: 'attempt_test', current_question_revision: 1,
    current_decision: { origin: 'model', validity: 'candidate', verdict: 'partially_correct', reason_code: null,
      input_versions: { question_id: 'question_test', question_revision: 1, attempt_id: 'attempt_test', attempt_recognition_revision: 1 },
      answer_checks: [
        { index: 1, prompt: '单位1是____', student_answer: '总量', verdict: 'correct', correct_answer: '总量', explanation: '是整体量。' },
        { index: 2, prompt: '用____法', student_answer: '加', verdict: 'incorrect', correct_answer: '除', explanation: '单位1未知。' },
      ], correct_answer: '总量；除法。', correction_method: '用对应量除以分率。' } },
  question: { question_id: 'question_test', label: '3', stem_text: '看图填空。', blank_count: 2 },
  attempt: { id: 'attempt_test', recognition_status: 'ready', recognition_revision: 1, answer_text: '总量；加' },
} as CaseDetail)

describe('per-blank student feedback', () => {
  it('shows correct and incorrect blanks separately within one wrong question', () => {
    const html = renderToStaticMarkup(<QuestionCard row={row()} index={0} open onToggle={() => {}} />)
    expect(html).toContain('aria-label="逐空判定"')
    expect(html).toContain('class="recognition-answer correct"')
    expect(html).toContain('class="recognition-answer incorrect"')
    expect(html).toContain('✓ 正确')
    expect(html).toContain('✕ 错误')
    expect(html).toContain('应填：除')
    expect(html).not.toContain('应填：总量')
    expect(html).toContain('<span class="recognition-label">正确答案</span>')
    expect(html).not.toContain('参考答案')
  })
  it('hides stale per-blank decisions after the student answer changes', () => {
    const value = row()
    value.attempt.recognition_revision = 2
    const html = renderToStaticMarkup(<QuestionCard row={value} index={0} open onToggle={() => {}} />)
    expect(html).not.toContain('aria-label="逐空判定"')
    expect(html).toContain('我的作答')
  })
})

const removedLegacySummary = '旧模型审核（完成 2/3 轮）：正确 67% / 错误 33%（模型估计，未经校准）；存在分歧或证据不足，按保守策略标错，请核对参考答案。'
const genericUncertainty = '* AI 对这道题的识别或判断存在不确定，请结合原图核对。'
const controlledServiceMessages = [
  '请先在学生档案设置已支持的年级，并选择本题学科。',
  '本题不在已配置的首发年级、学科和教材出版社范围内，暂不提供判题结论。',
  '模型复核仍未能确认本题对错，请核对题目和作答后重试。',
  '首轮与独立复核的结论不一致，请核对原题后重试。',
]

const uncertainRow = (summary = removedLegacySummary): CaseDetail => {
  const value = row()
  value.case.current_decision!.verdict = 'incorrect'
  value.case.current_decision!.uncertainties = [summary]
  return value
}

describe('controlled review uncertainty feedback', () => {
  it('shows only exact controlled messages and hides raw model text, keeping the star on the incorrect badge', () => {
    const message = controlledServiceMessages[2]
    const value = uncertainRow(message)
    value.case.current_decision!.uncertainties!.push('模型内部不确定文本：不应展示。')
    value.question.recognition_uncertainties = ['题目原始不确定文本：不应展示。']
    value.attempt.recognition_uncertainties = ['作答原始不确定文本：不应展示。']

    const html = renderToStaticMarkup(<QuestionCard row={value} index={0} open onToggle={() => {}} />)

    expect(html).toContain(`<p class="recognition-uncertainty">${message}</p>`)
    expect(html).toContain('<span class="recognition-label">正确答案</span>')
    expect(html).toContain('总量；除法。')
    expect(html).toContain('应填：除')
    expect(html).not.toContain('参考答案')
    expect(html).not.toContain(genericUncertainty)
    expect(html).not.toContain('不应展示')
    expect(html).toContain('<span class="recognition-status incorrect"><sup class="result-star" aria-label="AI识别不确定">*</sup>')
  })

  it('keeps review details collapsed while retaining the badge star', () => {
    const html = renderToStaticMarkup(<QuestionCard row={uncertainRow(controlledServiceMessages[3])} index={0} open={false} onToggle={() => {}} />)

    expect(html).toContain('aria-expanded="false"')
    expect(html).toContain('<span class="recognition-index">1</span>')
    expect(html).toContain('<span class="recognition-status incorrect"><sup class="result-star" aria-label="AI识别不确定">*</sup>')
    expect(html).not.toContain('recognition-card-body')
    expect(html).not.toContain(controlledServiceMessages[3])
    expect(html).not.toContain('正确答案')
  })

  it.each(controlledServiceMessages)('shows each exact allowlisted message: %s', message => {
    const html = renderToStaticMarkup(<QuestionCard row={uncertainRow(message)} index={0} open onToggle={() => {}} />)

    expect(html).toContain(`<p class="recognition-uncertainty">${message}</p>`)
    expect(html).not.toContain('参考答案')
    expect(html).not.toContain(removedLegacySummary)
  })

  it.each([
    removedLegacySummary,
    `${controlledServiceMessages[0]}模型补充：不应展示。`,
    '普通模型文本：这段原始解释不应展示。',
  ])('hides removed or malformed model text: %s', summary => {
    const html = renderToStaticMarkup(<QuestionCard row={uncertainRow(summary)} index={0} open onToggle={() => {}} />)

    expect(html).toContain(genericUncertainty)
    expect(html).not.toContain(summary)
    expect(html).toContain('<span class="recognition-label">正确答案</span>')
    expect(html).not.toContain('67%')
  })

  it('uses only the first exact controlled message and hides later raw uncertainties', () => {
    const value = uncertainRow(controlledServiceMessages[0])
    value.case.current_decision!.uncertainties!.push('原始模型不确定文本')
    value.question.recognition_uncertainties = [removedLegacySummary]
    value.attempt.recognition_uncertainties = ['作答原始不确定文本']
    const html = renderToStaticMarkup(<QuestionCard row={value} index={0} open onToggle={() => {}} />)

    expect(html).toContain(controlledServiceMessages[0])
    expect(html).not.toContain(removedLegacySummary)
    expect(html).not.toContain('原始模型不确定文本')
    expect(html).not.toContain('作答原始不确定文本')
  })

  it('does not reuse stale controlled messages after the student answer changes', () => {
    const value = uncertainRow(controlledServiceMessages[1])
    value.attempt.recognition_revision = 2
    const html = renderToStaticMarkup(<QuestionCard row={value} index={0} open onToggle={() => {}} />)

    expect(html).toContain('我的作答')
    expect(html).not.toContain(controlledServiceMessages[1])
    expect(html).not.toContain('正确答案')
    expect(html).not.toContain('recognition-correct-answer')
    expect(html).not.toContain('result-star')
  })

  it('uses ordinary answer wording when a review remains uncertain and has no answer', () => {
    const value = uncertainRow(controlledServiceMessages[2])
    value.case.current_decision!.correct_answer = null
    const html = renderToStaticMarkup(<QuestionCard row={value} index={0} open onToggle={() => {}} />)

    expect(html).toContain('暂未生成正确答案，请重新识别或请老师核对。')
    expect(html).not.toContain('参考答案')
  })
})
