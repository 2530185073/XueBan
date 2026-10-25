import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import type { CaseDetail } from '../core/types'
import type { Workspace } from '../useStudentWorkspace'
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
  it('keeps an unanswered blank neutral and distinct from an unreadable answer', () => {
    const value = row()
    value.question.blank_count = 3
    value.case.current_decision!.answer_checks = [
      { index: 1, prompt: '第一空', student_answer: '3', verdict: 'correct', correct_answer: null, explanation: '正确。' },
      { index: 2, prompt: '第二空', student_answer: null, verdict: 'unanswered', correct_answer: '不应提前显示的答案', explanation: '尚未作答。' },
      { index: 3, prompt: '第三空', student_answer: null, verdict: 'unable_to_judge', correct_answer: '不可靠的候选答案', explanation: '笔迹暂未读清。' },
    ]
    const html = renderToStaticMarkup(<QuestionCard row={value} index={0} open onToggle={() => {}} />)
    expect(html).toContain('class="recognition-answer unanswered"')
    expect(html).toContain('未作答 <span class="slot-verdict">待完成</span>')
    expect(html).toContain('class="recognition-answer uncertain"')
    expect(html).toContain('暂未读到清楚的作答 <span class="slot-verdict">待核对</span>')
    expect(html).not.toContain('✕ 错误')
    expect(html).not.toContain('class="recognition-answer incorrect"')
    expect(html).not.toContain('不应提前显示的答案')
    expect(html).not.toContain('不可靠的候选答案')
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


describe('correction controls in each result card', () => {
  const workspace = { busy: '', online: true, hint: null, client: {}, recordVersion: 0 } as unknown as Workspace
  it('keeps photo correction, album upload, graduated hints and teacher feedback reachable', () => {
    const html = renderToStaticMarkup(<QuestionCard row={row()} index={0} open onToggle={() => {}} workspace={workspace} onCorrection={() => {}} />)
    for (const text of ['我已在纸上订正', '从相册选择订正照片', '给我一点方向', '看看关键一步', '文字和照片不一样', '请老师帮忙']) expect(html).toContain(text)
    expect(html).toContain('可以拍整页')
    expect(html).toContain('订正记录')
  })
  it('never presents first-pass per-blank answers as settled after the overall judgment is unresolved', () => {
    const value = row(); value.case.current_decision!.validity = 'needs_review'; value.case.current_decision!.verdict = 'unable_to_judge'
    const html = renderToStaticMarkup(<QuestionCard row={value} index={0} open onToggle={() => {}} workspace={workspace} onCorrection={() => {}} />)
    expect(html).toContain('AI 还没确认这题'); expect(html).toContain('我的作答')
    expect(html).not.toContain('aria-label="逐空判定"'); expect(html).not.toContain('应填：'); expect(html).not.toContain('正确答案</span>')
    expect(html).toContain('我已在纸上订正')
  })
  it.each([
    ['provider_unavailable', 'AI 核对暂未完成'],
    ['reconciliation_unavailable', 'AI 核对暂未完成'],
    ['visual_unreadable', '这处笔迹暂时看不清，可补拍'],
    ['answer_disagreement', 'AI 还没确认这题'],
  ])('shows the completed unresolved %s result without an extra per-question model retry', (reason, label) => {
    const value = row(), decision = value.case.current_decision!
    decision.validity = 'needs_review'; decision.verdict = 'unable_to_judge'; decision.uncertainties = ['private provider detail']
    decision.verification = { mode: 'two_model', status: 'conflict', independent_readback: true, reason_codes: [reason] }
    value.attempt.recognition_status = 'needs_revision'
    const html = renderToStaticMarkup(<QuestionCard row={value} index={0} open onToggle={() => {}} workspace={workspace} onCorrection={() => {}} />)
    expect(html).toContain(label)
    expect(html).not.toMatch(/重新检查这道题|正在检查|正在再看|private provider detail|正确答案<|应填：/)
    expect(html).toContain('文字和照片不一样？请老师核对')
    expect(html).toContain('我已在纸上订正')
  })
  it('hides previous answer conclusions while a real recheck is running', () => {
    const value = row(); value.checkJob = { status: 'running' } as CaseDetail['checkJob']
    const html = renderToStaticMarkup(<QuestionCard row={value} index={0} open onToggle={() => {}} workspace={workspace} onCorrection={() => {}} />)
    expect(html).toContain('处理中'); expect(html).toContain('正在检查')
    expect(html).not.toMatch(/aria-label="逐空判定"|应填：|正确答案<|重新检查这道题/)
    const hints = html.match(/<button\b[\s\S]*?<\/button>/g)!.find(tag => tag.includes('给我一点方向'))
    expect(hints).toContain('disabled')
  })
  it('offers original-image recovery for a failed check while keeping the answer unjudged', () => {
    const value = row(); value.case.current_decision = null; value.checkJob = { status: 'failed' } as CaseDetail['checkJob']
    const html = renderToStaticMarkup(<QuestionCard row={value} index={0} open onToggle={() => {}} workspace={workspace} onCorrection={() => {}} />)
    expect(html).toContain('服务失败'); expect(html).toContain('重新核对原图'); expect(html).not.toContain('重新检查这道题')
    expect(html).not.toContain('正确答案</span>')
  })
})

describe('voluntary same-type practice entry', () => {
  const renderWithPractice = (value: CaseDetail) => renderToStaticMarkup(<QuestionCard row={value} index={0} open onToggle={() => {}} onVariant={() => {}} />)
  it('offers one voluntary question only from a current usable incorrect decision', () => {
    expect(renderWithPractice(row())).toContain('选做一道同题型')
    const uncertain = row()
    uncertain.case.current_decision!.validity = 'needs_review'
    expect(renderWithPractice(uncertain)).not.toContain('选做一道同题型')
    const stale = row()
    stale.attempt.recognition_revision = 2
    expect(renderWithPractice(stale)).not.toContain('选做一道同题型')
    const correct = row()
    correct.case.current_decision!.verdict = 'correct'
    expect(renderWithPractice(correct)).not.toContain('选做一道同题型')
  })
})
