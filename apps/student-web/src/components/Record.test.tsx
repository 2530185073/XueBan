import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import examples from '../../../../contracts/phase1/examples.json'
import type { CaseDetail, HelpRequest, HomeworkDetail, HomeworkSession } from '../core/types'
import type { Workspace } from '../useStudentWorkspace'
import { HistoryPage } from './HistoryPage'
import { AppealForm, HelpStatusList, Timeline } from './CaseRecord'
import { ResultsSummary } from './ResultsSummary'

const fixture = <T,>(name: string) => structuredClone(examples.entries.find(x => x.name === name)!.value) as T
const homework = (id: string, subject: string | null): HomeworkSession => ({ ...fixture<HomeworkSession>('homework'), id, curriculum: { ...fixture<HomeworkSession>('homework').curriculum, subject }, created_at: new Date().toISOString() })

describe('history page', () => {
  const workspace = {
    busy: '', drafts: [], cursor: null, history: [homework('homework_one', '数学'), homework('homework_two', null)], summaryFailed: ['homework_two'],
    summaries: { homework_one: { homeworkId: 'homework_one', revision: 3, counts: { total: 3, needs_correction: 1, pending_review: 1, candidate_correct: 0, confirmed_correct: 1 }, firstUnresolvedCaseId: 'case_one', retired: 1 } },
  } as unknown as Workspace
  const html = renderToStaticMarkup(<HistoryPage workspace={workspace} onOpen={() => {}} onOpenDraft={() => {}} onNewWork={() => {}} />)
  it('groups by day with filters, per-photo counts and continue entries', () => {
    expect(html).toContain('今天'); expect(html).toContain('全部科目'); expect(html).toContain('未标注科目'); expect(html).toContain('数学')
    for (const label of ['有待订正', '有待核实', '已完成']) expect(html).toContain(label)
    expect(html).toContain('本次拍到 3 题：需订正 1，待核实 1，已确认正确 1'); expect(html).toContain('只统计本次拍到的题目')
    expect(html.match(/继续订正/g)?.length).toBeGreaterThanOrEqual(2); expect(html).toContain('老师已重新切分部分题目')
    expect(html).toContain('题目统计暂时读不到'); expect(html).toContain('aria-pressed="true"')
  })
  it('renders loaded history and enables read-only refresh while model work owns the mutation lock', () => {
    const html = renderToStaticMarkup(<HistoryPage workspace={{ ...workspace, busy: '正在检查作业', historyLoading: false, historyLoaded: true }} onOpen={() => {}} onOpenDraft={() => {}} onNewWork={() => {}} />)
    const refresh = html.match(/<button[^>]*>[\s\S]*?刷新记录<\/button>/)?.[0]
    expect(refresh).not.toContain('disabled')
    expect(html).toContain(workspace.history[0].title!)
    expect(html).not.toContain('第一份作业，从今天开始')
    expect(html.match(/<button[^>]*class="history-open"[^>]*>/)?.[0]).toContain('disabled') // mutation-driven opening remains protected
  })
  it('does not claim history is empty while the first read is pending or failed', () => {
    for (const historyLoading of [true, false]) {
      const html = renderToStaticMarkup(<HistoryPage workspace={{ ...workspace, history: [], historyLoaded: false, historyLoading }} onOpen={() => {}} onOpenDraft={() => {}} onNewWork={() => {}} />)
      expect(html).not.toContain('第一份作业，从今天开始')
      expect(html).toContain(historyLoading ? '正在查找已经保存的作业' : '历史记录还没有读到')
    }
  })
  it('offers a visible local-draft clear control and no empty graph/report/practice entries', () => {
    expect(html).toContain('清除本机草稿'); expect(html).toContain('7 天')
    for (const absent of ['知识图谱', '学习报告', '练习', '正确率', '%']) expect(html).not.toContain(absent)
  })
})

describe('case record rendering', () => {
  it('strikes through invalid results and shows help status with the teacher note', () => {
    const html = renderToStaticMarkup(<Timeline entries={[{ key: 'decision:d', kind: 'decision', at: '2026-10-16T08:00:00Z', title: 'AI初判 · 需要订正', badges: [{ label: '已失效', tone: 'neutral' }], state: 'invalid' }, { key: 'decision:e', kind: 'decision', at: '2026-10-16T09:00:00Z', title: '老师确认 · 正确', badges: [{ label: '当前结果', tone: 'green' }], state: 'current' }]} />)
    expect(html).toContain('<del class="timeline-title">AI初判 · 需要订正</del>'); expect(html).toContain('timeline-item decision current'); expect(html).toContain('<ol class="timeline">')
    const help = { ...fixture<HelpRequest>('resolved_help'), kind: 'appeal' as const }
    const status = renderToStaticMarkup(<HelpStatusList items={[help, { ...help, id: 'help_waiting', status: 'waiting', claimed_by: null, claimed_at: null, completed_at: null, resolution_note: null }, { ...help, id: 'help_claimed', status: 'claimed', completed_at: null, resolution_note: null }]} />)
    expect(status).toContain('申请老师重查'); expect(status).toContain('老师已处理'); expect(status).toContain('已核对订正图与题干'); expect(status).toContain('等待老师'); expect(status).toContain('老师处理中')
  })
  it('appeal form has a labelled, bounded note and explicit submit', () => {
    const html = renderToStaticMarkup(<AppealForm disabled={false} onSubmit={() => {}} onCancel={() => {}} />)
    expect(html).toMatch(/<label for="[^"]+">想请老师重查的原因/); expect(html).toContain('maxLength="200"'); expect(html).toContain('提交重查申请')
  })
  it('summarises an opened partial homework without an accuracy figure', () => {
    const row = { case: { ...fixture<CaseDetail['case']>('resolved_case') }, question: fixture('question_revision_2'), attempt: fixture('correction_across_question_revision') } as CaseDetail
    const detail = { homework: fixture<HomeworkSession>('homework'), cases: [row], jobs: [], retired: [{ id: 'case_old', lifecycle: 'superseded', replacementCaseIds: ['case_synthetic'] }] } as HomeworkDetail
    const html = renderToStaticMarkup(<ResultsSummary rows={[row]} detail={detail} />)
    expect(html).toContain('本次拍到 1 题：需订正 0，待核实 0，已确认正确 1'); expect(html).toContain('仅采集一个题区'); expect(html).toContain('老师已重新切分此题'); expect(html).not.toContain('%')
  })
})
