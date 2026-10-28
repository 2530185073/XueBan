import { renderToStaticMarkup } from 'react-dom/server'
import { expect, it } from 'vitest'
import examples from '../../../../contracts/phase1/examples.json'
import type { CaseDetail, Decision, Job } from '../core/types'
import type { Workspace } from '../useStudentWorkspace'
import { studentServiceOnlyIncomplete } from '../presentation'
import { QuestionCard } from './RecognitionResults'
import { QuestionDetail } from './QuestionDetail'

const fixture = <T,>(name: string) => structuredClone(examples.entries.find(entry => entry.name === name)!.value) as T
const workspace = { busy: '', online: true, hint: null, client: {}, recordVersion: 0 } as unknown as Workspace
function sample(reasons = ['provider_unavailable', 'unmatched_label', 'uncertain_observation']): CaseDetail {
  const row: CaseDetail = { case: fixture('resolved_case'), question: fixture('question_revision_2'), attempt: fixture('correction_across_question_revision') }
  const d = fixture<Decision>('model_candidate')
  Object.assign(d, { origin: 'model', validity: 'needs_review', verdict: 'unable_to_judge', knowledge_points: [], correction_method: null,
    input_versions: { ...d.input_versions, question_id: row.case.question_id, question_revision: row.case.current_question_revision, attempt_id: row.case.current_attempt_id, attempt_recognition_revision: row.attempt.recognition_revision },
    verification: { mode: 'two_model', status: 'degraded', independent_readback: false, reason_codes: reasons }, uncertainties: ['internal unavailable note'],
  })
  row.case.current_decision = d; row.question.subject = null; row.question.options = []; row.attempt.recognition_status = 'needs_revision'; row.attempt.answer_status = 'present'; row.attempt.answer_text = 'A'; return row
}
const card = (row: CaseDetail, ws = workspace) => {
  const before = structuredClone(row), decision = row.case.current_decision
  const html = renderToStaticMarkup(<QuestionCard row={row} index={0} open onToggle={() => {}} workspace={ws} onCorrection={() => {}} onContinue={() => {}} onVariant={() => {}} />)
  expect(row).toEqual(before)
  expect(row.case.current_decision).toBe(decision)
  expect(html).not.toMatch(/question-detail|service-incomplete-detail|hint-section|help-panel|case-history|求助与重查|订正记录|选做一道同题型|查看答案与解法/)
  expect(html.match(/<button\b/g)).toHaveLength(1)
  return html
}
const detail = (row: CaseDetail, embedded: boolean, ws = workspace) => renderToStaticMarkup(<QuestionDetail row={row} embedded={embedded} workspace={ws} onCorrection={() => {}} onUpload={() => {}} onContinue={() => {}} />)
const button = (html: string, label: string) => html.match(/<button\b[\s\S]*?<\/button>/g)?.find(tag => tag.includes(label))

it('keeps a simple service card unjudged with its exact transcription and no embedded detail', () => {
  const row = sample(), before = structuredClone(row), html = card(row)
  expect(studentServiceOnlyIncomplete(row)).toBe(true)
  expect(html).toContain('<p>A</p>')
  expect(html).toContain('class="recognition-status uncertain"'); expect(html).toContain('class="recognition-answer uncertain"')
  expect(html).not.toMatch(/正确答案<|正确解题步骤|✓ 正确|✕ 错误|internal unavailable note/)
  expect(row).toEqual(before)
})
it('keeps the independent embedded service detail compact with one status and no solution hints', () => {
  const row = sample(), before = structuredClone(row), html = detail(row, true)
  expect(html).toContain('service-incomplete-detail')
  expect(html.match(/核对服务没有完成/g)).toHaveLength(1)
  expect(html).not.toMatch(/hint-section|给我一点方向|正确答案<|internal unavailable note/)
  expect(row).toEqual(before)
})
it('keeps original-image recovery and navigation reachable in the independent detail', () => {
  const html = detail(sample(), true)
  const fold = html.indexOf('<details class="service-supplement"')
  expect(fold).toBeGreaterThan(-1)
  expect(html).toContain('aria-label="原图核对"'); expect(html).toContain('先看其他题'); expect(html).toContain('请老师帮忙')
  expect(html.indexOf('aria-label="原图核对"')).toBeLessThan(fold)
  expect(html.indexOf('先看其他题')).toBeLessThan(fold); expect(html.indexOf('请老师帮忙')).toBeLessThan(fold)
  expect(html).toContain('让 AI 再看一次当前原图'); expect(html).toContain('重新核对原图')
  expect(html.match(/<details class="service-supplement"[^>]*>/)?.[0]).not.toContain('open')
  for (const label of ['我已在纸上订正，拍新作答', '从相册选择订正照片', '文字和照片不一样']) expect(button(html, label)).toBeDefined()
})
it('leaves independent detail history and help outside its collapsed action group', () => {
  const html = detail(sample(), true), close = html.indexOf('</details>')
  expect(close).toBeGreaterThan(-1)
  expect(html.indexOf('class="help-panel"')).toBeGreaterThan(close)
  expect(html.indexOf('class="case-history"')).toBeGreaterThan(close)
  expect(html).toContain('<details class="case-history" open="">'); expect(html).toContain('订正记录'); expect(html).toContain('申请老师重查')
})
it('retains real knowledge tags instead of hiding all learning information', () => {
  const row = sample(); row.case.current_decision!.knowledge_points = ['幂的意义']
  expect(card(row)).toContain('幂的意义'); expect(card(row)).toContain('recognition-learning')
})
it.each([{ reasons: ['visual_unreadable'] }, { reasons: ['provider_unavailable', 'visual_unreadable'] }, { reasons: ['answer_disagreement'] }])('does not compact a handwriting/other-only result in the independent detail: $reasons', ({ reasons }) => {
  const row = sample(reasons), html = detail(row, true)
  expect(studentServiceOnlyIncomplete(row)).toBe(false); expect(html).not.toContain('service-incomplete-detail')
  expect(html).toContain('hint-section'); expect(html).toContain('文字和照片不一样')
  expect(button(html, '给我一点方向')).toContain('disabled')
  expect(card(row)).toContain('class="recognition-answer uncertain"')
  expect(card(row)).not.toMatch(/正确答案<|正确解题步骤|✓ 正确|✕ 错误/)
})
it.each(['stale', 'other_attempt', 'other_revision', 'staff', 'usable', 'inactive', 'processing'] as const)('does not use old or inapplicable service reasons: %s', change => {
  const row = sample(), d = row.case.current_decision!
  if (change === 'stale') d.validity = 'stale'
  if (change === 'other_attempt') d.input_versions.attempt_id = 'attempt_old'
  if (change === 'other_revision') row.attempt.recognition_revision += 1
  if (change === 'staff') d.origin = 'staff'
  if (change === 'usable') { d.validity = 'candidate'; d.verdict = 'correct' }
  if (change === 'inactive') row.case.lifecycle = 'superseded'
  if (change === 'processing') row.checkJob = { status: 'running' } as Job
  expect(studentServiceOnlyIncomplete(row)).toBe(false); expect(detail(row, true)).not.toContain('service-incomplete-detail')
  const html = card(row)
  if (change !== 'usable') expect(html).not.toMatch(/class="recognition-answer (correct|incorrect)"|正确答案<|正确解题步骤|✓ 正确|✕ 错误/)
})
it('keeps the standalone detail and card without embedded actions unchanged', () => {
  const row = sample(), html = detail(row, false)
  expect(html).not.toContain('service-incomplete-detail'); expect(html).toContain('hint-section'); expect(html).toContain('result-callout')
  const plain = renderToStaticMarkup(<QuestionCard row={row} index={0} open onToggle={() => {}} />)
  expect(plain).toContain('recognition-learning'); expect(plain).toContain('recognition-uncertainty')
})
it('keeps busy mutation controls disabled in the independent detail while navigation stays usable', () => {
  const html = detail(sample(), true, { ...workspace, busy: '正在处理' })
  for (const name of ['请老师帮忙', '我已在纸上订正', '从相册选择订正照片', '文字和照片不一样']) expect(button(html, name)).toContain('disabled')
  expect(button(html, '先看其他题')).not.toContain('disabled')
  expect(card(sample(), { ...workspace, busy: '正在处理' })).not.toMatch(/我已在纸上订正|从相册选择订正照片|文字和照片不一样|请老师帮忙/)
})
