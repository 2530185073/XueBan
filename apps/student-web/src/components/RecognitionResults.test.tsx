import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'
import type { CaseDetail } from '../core/types'
import type { CurriculumScope } from '../core/learning'
import type { Workspace } from '../useStudentWorkspace'
import { QuestionCard, RecognitionResults } from './RecognitionResults'

vi.mock('./QuestionDetail', () => ({ QuestionDetail: () => { throw new Error('Result cards must not mount QuestionDetail') } }))
vi.mock('./CaseRecord', () => ({ CaseRecordPanel: () => { throw new Error('Result cards must not mount CaseRecordPanel') } }))

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

describe('compact question verdict labels', () => {
  it.each([
    ['model', 'correct', '正确'],
    ['model', 'incorrect', '错误'],
    ['staff', 'correct', '正确'],
    ['staff', 'incorrect', '错误'],
  ] as const)('uses only the short label for %s %s', (origin, verdict, label) => {
    const value = row(), decision = value.case.current_decision!
    Object.assign(decision, { origin, verdict, validity: origin === 'staff' ? 'confirmed' : 'candidate', answer_checks: [] })
    const before = structuredClone(value)
    const html = renderToStaticMarkup(<QuestionCard row={value} index={0} open={false} onToggle={() => {}} />)
    const badge = html.match(new RegExp(`<span class="recognition-status ${verdict}">[\\s\\S]*?</span>`))?.[0]
    expect(badge).toContain(`</svg>${label}</span>`)
    expect(badge).not.toMatch(/AI 初判|老师确认|需要订正/)
    expect(value).toEqual(before)
  })
})

describe('per-blank student feedback', () => {
  it('omits wrong-answer corrections and the reveal action for a correct answer', () => {
    const value = row()
    value.case.current_decision!.verdict = 'correct'
    value.case.current_decision!.answer_checks = []
    const before = structuredClone(value)
    const html = renderToStaticMarkup(<QuestionCard row={value} index={0} open onToggle={() => {}} />)
    expect(html).toContain('class="recognition-answer correct"')
    expect(html).not.toMatch(/查看答案与解法|正确答案|正确解题步骤/)
    expect(html).not.toContain('用对应量除以分率。')
    expect(html).not.toContain('总量；除法。')
    expect(value).toEqual(before)
  })
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
    expect(html).not.toContain('查看答案与解法')
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
    expect(html).toContain('未作答 <span class="slot-verdict">未作答</span>')
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
    expect(html).not.toContain('查看答案与解法')
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
    expect(html).not.toContain('查看答案与解法')
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
    expect(html).not.toContain('查看答案与解法')
    expect(html).not.toContain('参考答案')
  })
})


describe('simple result cards', () => {
  const workspace = { busy: '', online: true, hint: null, client: {}, recordVersion: 0 } as unknown as Workspace
  const legacyProps = { workspace, onCorrection: vi.fn(), onContinue: vi.fn(), onVariant: vi.fn(), variantBusy: true }
  const render = (value: CaseDetail, open = true) => {
    const before = structuredClone(value), decision = value.case.current_decision
    const html = renderToStaticMarkup(<QuestionCard row={value} index={0} open={open} onToggle={() => {}} {...legacyProps} />)
    expect(value).toEqual(before)
    expect(value.case.current_decision).toBe(decision)
    for (const text of ['查看答案与解法', '给我一点方向', '看看关键一步', '我已在纸上订正', '从相册选择订正照片', '文字和照片不一样', '请老师帮忙', '求助与重查', '订正记录', '选做一道同题型', '重新核对原图', '重新检查这道题', '查看原文', '复制本题原文']) expect(html).not.toContain(text)
    expect(html).not.toMatch(/question-detail|hint-section|help-panel|case-history|recognition-variant-action|math-view-tools|<details|<textarea/)
    expect(html.match(/<button\b/g)).toHaveLength(1)
    return html
  }
  it('directly shows the original question, answer colors, knowledge and solution steps', () => {
    const value = row()
    value.case.current_decision!.knowledge_points = ['分数除法']
    const html = render(value)
    for (const text of ['看图填空。', '总量', '加', '知识点', '分数除法', '正确答案', '总量；除法。', '正确解题步骤', '用对应量除以分率。']) expect(html).toContain(text)
    expect(html).toContain('class="recognition-answer correct"')
    expect(html).toContain('class="recognition-answer incorrect"')
  })
  it('keeps the header and question stem separate from the original choices', () => {
    const value = row()
    value.question.stem_text = '3、选择表示整体的词语。'
    value.question.options = [{ label: 'D', text: '整体量' }, { label: 'B', text: '部分量' }]
    const html = render(value)
    expect(html).toContain('<span class="recognition-card-title">选择表示整体的词语。</span>')
    const question = html.match(/<div class="recognition-question-block">[\s\S]*?<\/div>/)?.[0]
    expect(question).toContain('<p>选择表示整体的词语。</p>')
    expect(question).not.toMatch(/整体量|部分量/)
    expect(html).toContain('<li><strong>D</strong><span>整体量</span></li>')
    expect(html).toContain('<li><strong>B</strong><span>部分量</span></li>')
    const collapsed = render(value, false)
    expect(collapsed).not.toMatch(/整体量|部分量|recognition-original-options/)
  })
  it.each(['correct', 'incorrect'] as const)('keeps the star inside the short %s badge without changing a usable decision', verdict => {
    const value = uncertainRow()
    value.case.current_decision!.verdict = verdict
    value.case.current_decision!.answer_checks = []
    const html = render(value)
    expect(html).toContain(`<span class="recognition-status ${verdict}"><sup class="result-star" aria-label="AI识别不确定">*</sup>`)
    expect(html).toContain(`</svg>${verdict === 'correct' ? '正确' : '错误'}</span>`)
    expect(html).toContain(`class="recognition-answer ${verdict}"`)
    if (verdict === 'correct') expect(html).not.toMatch(/正确答案|正确解题步骤/)
  })
  it('never presents first-pass per-blank answers as settled after the overall judgment is unresolved', () => {
    const value = row(); value.case.current_decision!.validity = 'needs_review'; value.case.current_decision!.verdict = 'unable_to_judge'
    const html = render(value)
    expect(html).toContain('class="recognition-status uncertain"'); expect(html).toContain('我的作答')
    expect(html).not.toMatch(/逐空判定|应填：|正确答案<|正确解题步骤|✓ 正确|✕ 错误/)
  })
  it.each([
    'provider_unavailable', 'reconciliation_unavailable', 'visual_unreadable', 'answer_disagreement',
  ])('keeps the completed unresolved %s result neutral with no extra workflow', reason => {
    const value = row(), decision = value.case.current_decision!
    decision.validity = 'needs_review'; decision.verdict = 'unable_to_judge'; decision.uncertainties = ['private provider detail']
    decision.verification = { mode: 'two_model', status: 'conflict', independent_readback: true, reason_codes: [reason] }
    value.attempt.recognition_status = 'needs_revision'
    const html = render(value)
    expect(html).toContain('class="recognition-status uncertain"')
    expect(html).toContain('class="recognition-answer uncertain"')
    expect(html).not.toMatch(/正在检查|正在再看|private provider detail|正确答案<|应填：|正确解题步骤|✓ 正确|✕ 错误/)
  })
  it.each(['correct', 'incorrect'] as const)('hides a previous %s conclusion while a real recheck is running', verdict => {
    const value = row(); value.case.current_decision!.verdict = verdict; value.checkJob = { status: 'running' } as CaseDetail['checkJob']
    const html = render(value)
    expect(html).toContain('处理中')
    expect(html).toContain('class="recognition-answer processing"')
    expect(html).not.toMatch(/逐空判定|应填：|正确答案<|正确解题步骤|✓ 正确|✕ 错误/)
  })
  it('keeps a failed check unjudged without additional workflows', () => {
    const value = row(); value.case.current_decision = null; value.checkJob = { status: 'failed' } as CaseDetail['checkJob']
    const html = render(value)
    expect(html).toContain('服务失败')
    expect(html).toContain('class="recognition-answer service_failure"')
    expect(html).not.toMatch(/逐空判定|正确答案<|正确解题步骤|✓ 正确|✕ 错误/)
  })
  it.each(['stale', 'retracted', 'different_attempt', 'different_question', 'different_revision', 'inactive'] as const)('does not display a %s decision as a settled answer', change => {
    const value = row(), decision = value.case.current_decision!
    if (change === 'stale' || change === 'retracted') decision.validity = change
    if (change === 'different_attempt') decision.input_versions.attempt_id = 'attempt_old'
    if (change === 'different_question') decision.input_versions.question_id = 'question_old'
    if (change === 'different_revision') value.attempt.recognition_revision += 1
    if (change === 'inactive') value.case.lifecycle = 'superseded'
    const html = render(value)
    expect(html).toContain('尚未检查')
    expect(html).not.toMatch(/逐空判定|正确答案<|正确解题步骤|✓ 正确|✕ 错误/)
  })
})

describe('correct solution steps', () => {
  const render = (steps: string | null | undefined, verdict: 'incorrect' | 'partially_correct' | 'unanswered' = 'incorrect') => {
    const value = row(), decision = value.case.current_decision!
    decision.correction_method = steps; decision.verdict = verdict
    const before = structuredClone(value)
    const html = renderToStaticMarkup(<QuestionCard row={value} index={0} open onToggle={() => {}} />)
    expect(value).toEqual(before)
    expect(value.case.current_decision).toBe(decision)
    return html
  }
  it.each(['incorrect', 'partially_correct'] as const)('directly shows backend calculation steps for a usable %s answer', verdict => {
    const steps = '1/15×10＝2/3\n1－2/3＝1/3\n1/3÷1/15＝5（天）'
    const html = render(steps, verdict)
    expect(html).toContain('正确解题步骤</span>')
    expect(html).toContain(`<p class="recognition-solution-steps">${steps}</p>`)
    expect(html).not.toMatch(/改正方法|查看答案与解法/)
  })
  it('keeps an unanswered question separate from incorrect and uncertain results', () => {
    const html = render('−3＋5＝2', 'unanswered')
    expect(html).toContain('class="recognition-status unanswered"')
    expect(html).toContain('class="recognition-answer unanswered"')
    expect(html).toContain('未作答')
    expect(html).not.toMatch(/正确答案|正确解题步骤|应填：|✓ 正确|✕ 错误|待确认|result-star/)
  })
  it.each([
    '  1/15×10＝2/3\n\n  1－2/3＝1/3\n',
    '1/15×10＝2/3\r\n1－2/3＝1/3\r\n1/3÷1/15＝5（天）',
    '先通分，再计算。',
  ])('keeps backend whitespace and prose without inventing or splitting steps: %s', steps => {
    const html = render(steps)
    expect(html).toContain(`<p class="recognition-solution-steps">${steps}</p>`)
    expect(html.match(/class="recognition-solution-steps"/g)).toHaveLength(1)
  })
  it('retains long backend steps without truncating them', () => {
    const expression = `${'123456789/987654321＋'.repeat(300)}1`
    const steps = Array.from({ length: 20 }, (_, index) => `${index + 1}. ${expression}`).join('\n')
    expect(render(steps)).toContain(`<p class="recognition-solution-steps">${steps}</p>`)
  })
  it.each([null, undefined, '', ' \n\t\r\n '])('omits empty solution steps without inventing a process: %s', steps => {
    const html = render(steps)
    expect(html).not.toMatch(/正确解题步骤|recognition-solution-steps/)
  })
  it('escapes markup-shaped backend steps', () => {
    const html = render('x < 3\n<script>alert(1)</script>\n<img src=x onerror="bad()">')
    expect(html).toContain('x &lt; 3\n&lt;script&gt;alert(1)&lt;/script&gt;\n&lt;img src=x onerror=&quot;bad()&quot;&gt;')
    expect(html).not.toMatch(/<script|<img/)
  })
})

describe('original recognition results layout', () => {
  const workspace = { busy: '', studentCurriculum: null } as unknown as Workspace
  const subjects = [{ subject: '数学', publisher_name: '人教版', catalog_status: 'available' }] as CurriculumScope[]

  it('starts entirely collapsed with continuous numbering and only the next-question action', () => {
    const rows = Array.from({ length: 3 }, (_, index) => {
      const value = row()
      value.case.id = `case_${index}`; value.question.label = ['8', '3', '21'][index]
      return value
    })
    const before = structuredClone(rows)
    const html = renderToStaticMarkup(<RecognitionResults rows={rows} workspace={workspace} onNext={() => {}} onSelectSubject={() => {}} subjects={subjects} selectedCaseId={rows[0].case.id} onSelect={() => {}} onCorrection={() => {}} onContinue={() => {}} onVariant={() => {}} variantBusy={rows[0].case.id} />)
    expect(html).toContain('本次作答 3 题 · 正确 0 道 · 错误 3 道')
    expect(html.match(/class="recognition-index">\d+<\/span>/g)).toEqual(['class="recognition-index">1</span>', 'class="recognition-index">2</span>', 'class="recognition-index">3</span>'])
    expect(html.match(/aria-expanded="false"/g)).toHaveLength(3)
    expect(html).not.toContain('recognition-card-body')
    expect(html).toContain('正确/错误右上角的 *')
    expect(html).toContain('知识点和正确解题步骤')
    expect(html).toContain('拍下一题')
    expect(html).not.toMatch(/拍下一份作业|选做一道同题型|查看答案与解法|question-detail|求助与重查|订正记录|老师确认|AI 初判/)
    expect(html.match(/<button\b/g)).toHaveLength(4)
    expect(rows).toEqual(before)
  })
  it('retains manual subject selection for an unknown subject and disables it while busy', () => {
    const value = row()
    const render = (busy: string) => renderToStaticMarkup(<QuestionCard row={value} index={0} open onToggle={() => {}} subjects={subjects} onSelectSubject={() => {}} workspace={{ ...workspace, busy }} />)
    const html = render('')
    expect(html).toContain('aria-label="选择题目学科"')
    expect(html).toContain('<option value="数学">数学 · 人教版</option>')
    expect(html.match(/<select[^>]*>/)?.[0]).not.toContain('disabled')
    expect(render('正在保存').match(/<select[^>]*>/)?.[0]).toContain('disabled')
    value.question.subject = '数学'
    expect(render('')).not.toContain('选择题目学科')
  })
  it('disables the next-question action while the workspace is busy', () => {
    const html = renderToStaticMarkup(<RecognitionResults rows={[]} workspace={{ ...workspace, busy: '正在检查作业' }} onNext={() => {}} onSelectSubject={() => {}} />)
    expect(html.match(/<button[^>]*class="button primary next-capture"[^>]*>/)?.[0]).toContain('disabled')
  })
})
