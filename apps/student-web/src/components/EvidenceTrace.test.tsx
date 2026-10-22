import { renderToStaticMarkup } from 'react-dom/server'
import { expect, it } from 'vitest'
import { traceFixture } from '../core/evidenceTrace.fixtures'
import { resolveEvidence } from '../core/evidenceTrace'
import type { Workspace } from '../useStudentWorkspace'
import { EvidenceTraceContent, EvidenceTracePanel } from './EvidenceTrace'
import { KnowledgeDetailView } from './KnowledgeMap'
const workspace = { client: { evidenceScope: 'scope_synthetic' }, studentId: 'student_synthetic' } as unknown as Workspace
it.each([0, 1])('renders historical answer %s untruncated, with captured question/options and qualified result', index => {
  const f = traceFixture(), selected = resolveEvidence(f.focus(index), f.studentId, f.detail, f.record)
  selected.attempt.answer_text += '\n' + '原始笔迹 '.repeat(50) + '<script>alert(1)</script>'
  const trace = { ...selected, question: f.questions[index] }, before = structuredClone(trace), html = renderToStaticMarkup(<EvidenceTraceContent trace={trace} workspace={workspace} scope="scope_synthetic" />)
  expect(html).toContain(`data-attempt-id="${selected.attempt.id}"`); expect(html).toContain('当时的 AI 初判'); expect(html).toContain('不表示已经独立掌握')
  expect(html).toContain('原始笔迹 '.repeat(50)); expect(html).toContain('&lt;script&gt;'); expect(html).not.toContain('<script>')
  expect(html).toContain(index ? '第1次订正' : '已定位 · 原作答')
  if (!index) { expect(html.indexOf('>B<')).toBeLessThan(html.indexOf('>A<')); expect(html).not.toContain('新版题面') }
  expect(html).not.toContain('当前题面不得替代旧题面'); expect(trace).toEqual(before)
})
it('missing answer stays unknown rather than asserting student left it blank', () => {
  const f = traceFixture(), selected = resolveEvidence(f.focus(), f.studentId, f.detail, f.record); selected.attempt.answer_text = null
  const html = renderToStaticMarkup(<EvidenceTraceContent trace={{ ...selected, question: f.questions[0] }} workspace={workspace} scope="scope_synthetic" />)
  expect(html).toContain('不据此认定未作答'); expect(html).not.toContain('未填写作答')
})
it('initial panel does not show current photo or correction actions', () => {
  const f = traceFixture(), html = renderToStaticMarkup(<EvidenceTracePanel focus={f.focus()} workspace={workspace} onClose={() => {}} />)
  expect(html).toContain('正在核对这次作答与原照片'); expect(html).toContain('返回当前作业'); expect(html).not.toMatch(/我已在纸上订正|本次作答照片|新版题面|= -2/)
})
it('knowledge detail retains both evidence rows and AI qualification', () => {
  const f = traceFixture(), html = renderToStaticMarkup(<KnowledgeDetailView detail={f.detail} onBack={() => {}} onOpenHomework={() => {}} />)
  expect(html.match(/查看原题与订正/g)).toHaveLength(2); expect(html.match(/AI 初判/g)).toHaveLength(2)
})
