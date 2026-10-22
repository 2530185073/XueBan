import { describe, expect, it, vi } from 'vitest'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { StaffApi } from './api/client'
import { LearningApi } from './api/learning'
import {
  isAgentRun, isCreatedShareLink, isEvidence, isGate, isGenerationStatus, isKnowledgeDetail, isKnowledgeLinkList, isLinkQueue, isMemoryList, isReport, isReportQueue, isShareLink, isStudentKnowledge, type AgentRun, type KnowledgeLink, type ReportSnapshot,
} from './api/learningTypes'
import {
  diagnosisIssue, finishedPeriods, helpSeen, lastFinishedPeriod, memoryIssue, parseAnswerValues, periodFinished, periodFromStart, periodLabel, primaryConflict,
  dailyMessage, dayLabel, deliveryLabel, evidenceSourceCounts, evidenceSourceLabel, recentDays, reportJudgmentCounts, runView, shareState, shareUrl, statusLabel, statusLabels,
} from './learning'
import { sampleWorkspace } from './sample'
import { generationText } from './components/PracticeArea'
import { DailySnapshotView } from './components/DailyFeedbackArea'
import { SnapshotView } from './components/ReportDetail'
import { EvidenceRow } from './components/KnowledgeDetailPanel'

const at = '2026-10-16T08:00:00+00:00'
const link = (overrides: Partial<KnowledgeLink> = {}): KnowledgeLink => ({
  id: 'qkl_1', question_id: 'question_a', knowledge_id: 'kp-rational-add-sub', knowledge_name: '有理数加减', role: 'primary', source: 'staff', status: 'confirmed',
  rationale: null, actor_id: 'staff_a', supersedes_id: null, created_at: at, ...overrides,
})
const state = {
  knowledge_id: 'kp-rational-add-sub', learning_status: 'needs_help_now', reason_codes: ['single_error_needs_diagnosis'], evidence_sufficiency: 'limited',
  sufficiency_explanation: '只有作业记录，独立完成情况未核实', valid_evidence_count: 1, scorable_evidence_count: 1, independent_evidence_count: 0, independent_dates_count: 0,
  distinct_item_families_count: 0, observed_error_count: 1, last_error_at: at, last_independent_success_at: null, delayed_verified_at: null, review_due_at: null,
  diagnoses: [], open_questions: [{ code: 'independence_unverified', count: 1 }, { code: 'cause_not_diagnosed', count: 1 }], evidence_ids: ['evidence_1'], rule_version: 'r1.1-custody',
}
const evidence = {
  id: 'evidence_1', attempt_id: 'attempt_a', case_id: 'case_a', homework_id: 'homework_a', question_id: 'question_a', decision_id: 'decision_a', verdict: 'incorrect',
  event_type: 'school_first_attempt', result: 'incorrect', assistance_level: 'unknown', independent: false, link_role: 'primary', occurred_at: at,
  validity: 'valid', invalidation_reason: null, question_text: '1 + 1 = ?', homework_title: null,
}
const node = { id: 'kp-rational-add-sub', node_type: 'concept', parent_id: 'ch-7a-02', name: '有理数加减', definition: null, observable_objective: '能计算', curriculum_refs: [], coverage_facets: [], misconception_codes: [], sort_order: 9 }
const snapshot = {
  rule_version: 'report-r1', period: { start: '2026-10-01', end_exclusive: '2026-10-16', timezone: 'Asia/Shanghai' },
  coverage: { observed_days: 1, observed_dates: ['2026-10-08'], homeworks: 1, partial_period: true, first_seen_date: '2026-10-08' },
  school_tasks: { questions: 1, staff_verified: 1, awaiting_verification: 0, not_judged: 0, verdicts: { correct: 0, incorrect: 1, partially_correct: 0, unanswered: 0 }, corrected_resolved: 0, correction_open: 1 },
  help: { requests: 0, by_kind: {}, resolved: 0, hints_viewed: 0, answers_shown: 0, staff_help: [] },
  focus: [{ knowledge_id: 'kp-rational-add-sub', name: '有理数加减', errors: 1, denominator: 1, independent_errors: 0, counter_evidence: 0, assistance: { 帮助情况未知: 1 },
    status_at_period_end: 'needs_help_now', status_label: '当前需要帮助', verified_scope: [], possible_causes: ['可能是异号相加取符号'], unknown: ['independence_unverified'],
    cards: [{ evidence_id: 'evidence_1', date: '2026-10-08', homework_title: null, page: 1, question: '1 + 1 = ?', result: 'incorrect', assistance: '帮助情况未知', staff_explanation: '已核对原图及过程。', certainty: '老师已核实' }],
    next_step: '后续学校作业遇到相关步骤时，由坐班老师现场观察和答疑；家长无需额外布题。' }],
  steady: [], unknown_current: [], sufficient: true, evidence_ids: ['evidence_1'], sections: { optional_activity: { sessions: 0, minutes: 0, answered: 0, correct: 0, exited: 0, summary: '本期没有安排额外活动。' } },
  limitations: '本报告只汇总本期在托管班拍照、经老师核实的学校作业。',
}
const report = {
  id: 'report_1', student_id: 'student_a', cadence: 'half_month', period_start: '2026-10-01', period_end: '2026-10-16', version: 2, status: 'draft', snapshot, staff_note: null, next_step: null,
  correction_note: null, supersedes_id: 'report_0', published_at: null, revision: 3, regenerated_reason: 'correction_required', published_by: null, source_digest: 'x', created_at: at, updated_at: at,
}

describe('学情接口的手写校验', () => {
  it('知识点关联与待确认队列', () => {
    expect(isKnowledgeLinkList({ question_id: 'question_a', question_revision: 2, graph_version: 'graph-1', items: [link(), link({ id: 'qkl_2', status: 'candidate', source: 'model', rationale: '整数加法' })] })).toBe(true)
    expect(isKnowledgeLinkList({ question_id: 'question_a', question_revision: 2, graph_version: 'graph-1', items: [{ ...link(), role: 'main' }] })).toBe(false)
    expect(isLinkQueue({ items: [{ case_id: 'case_a', question_id: 'question_a', student_id: 'student_a', homework_title: null, question_text: null, verdict: 'incorrect', candidates: [] }] })).toBe(true)
    expect(isLinkQueue({ items: [{ case_id: 'case_a' }] })).toBe(false)
  })
  it('学员知识状态：状态只能是四种之一，不接受分数', () => {
    const body = { student_id: 'student_a', graph_version: 'pep-1', graph_review_status: 'pilot_seed_unreviewed', rule_version: 'r1.1',
      projection: { evidence_revision: 1, state_version: 1, projected_at: at, pending: false }, states: [{ ...state, name: '有理数加减', chapter_id: 'ch-7a-02', chapter_name: '有理数', sort_order: 9 }],
      chapters: [{ chapter_id: 'ch-7a-02', name: '有理数', objectives: 3, status_counts: { needs_help_now: 1, no_judgment: 2 } }], notice: '状态只根据坐班老师已核实的作答计算。' }
    expect(isStudentKnowledge(body)).toBe(true)
    expect(isStudentKnowledge({ ...body, states: [{ ...body.states[0], learning_status: 'mastered' }] })).toBe(false)
    expect(isStudentKnowledge({ ...body, chapters: [{ ...body.chapters[0], status_counts: { mastery: 0.6 } }] })).toBe(false)
    expect(isStudentKnowledge({ ...body, projection: { ...body.projection, pending: 'no' } })).toBe(false)
    expect(isStudentKnowledge({ ...body, graph_version: null, graph_review_status: 'not_ready_latest_edition', states: [], chapters: [] })).toBe(true)
    expect(isStudentKnowledge({ ...body, graph_version: undefined })).toBe(false)
    expect(isStudentKnowledge({ ...body, states: [{ ...body.states[0], model_evidence_count: -1 }] })).toBe(false)
    expect(isStudentKnowledge({ ...body, states: [{ ...body.states[0], assessment_source: 'parent' }] })).toBe(false)
  })
  it('知识点详情含证据、前置与失效原因', () => {
    const detail = { node, state, evidence: [evidence, { ...evidence, id: 'evidence_0', validity: 'invalidated', invalidation_reason: 'superseded' }],
      prerequisites: [{ id: 'kp-opposite-abs', name: '相反数与绝对值', learning_status: 'no_judgment' }], leads_to: [], notice: '前置关系只用于提出诊断问题。' }
    expect(isKnowledgeDetail(detail)).toBe(true)
    expect(isKnowledgeDetail({ ...detail, evidence: [{ ...evidence, validity: 'maybe' }] })).toBe(false)
    expect(isKnowledgeDetail({ ...detail, state: null })).toBe(true)
    expect(isKnowledgeDetail({ ...detail, evidence: [{ ...evidence, source_role: 'model' }] })).toBe(true)
    expect(isKnowledgeDetail({ ...detail, evidence: [{ ...evidence, source_role: 'unknown' }] })).toBe(true)
    expect(isKnowledgeDetail({ ...detail, evidence: [{ ...evidence, source_role: 'unknown_provider' }] })).toBe(false)
  })
  it('记忆与 AI 摘要', () => {
    const memory = { id: 'memory_1', category: 'effective_support', content: '先口述符号法则再计算', knowledge_id: null, evidence_ids: ['evidence_1'], source: 'model', status: 'proposed',
      status_reason: null, confirmed_by: null, confirmed_at: null, valid_until: null, supersedes_id: null, revision: 1, created_at: at }
    expect(isMemoryList({ student_id: 'student_a', items: [memory], notice: '学伴只记住有出处的事实。' })).toBe(true)
    expect(isMemoryList({ student_id: 'student_a', items: [{ ...memory, source: 'parent' }], notice: '' })).toBe(false)
    const run = { id: 'agentrun_1', purpose: 'staff_summary', status: 'completed', context: { evidence_ids: ['evidence_1'], state_version: 1 }, model_metadata: null, failure_code: null, created_at: at, completed_at: at,
      output: { status: 'ready', summary: '有一次经核实的错误。', next_action: '订正时先写出符号法则。', claims: [{ text: '1 道加法题答错', evidence_ids: ['evidence_1'], certainty: 'high' }], memory_proposals: [] } }
    expect(isAgentRun(run)).toBe(true)
    expect(isAgentRun({ ...run, status: 'queued', context: {}, output: null, completed_at: null })).toBe(true)
    expect(isAgentRun({ ...run, output: { ...run.output, claims: [{ text: 'x', evidence_ids: ['evidence_1'], certainty: 0.9 }] } })).toBe(false)
  })
  it('半月报告快照：真实分母与更正版本', () => {
    expect(isReport(report)).toBe(true)
    expect(isReport({ ...report, snapshot: { ...snapshot, school_tasks: { ...snapshot.school_tasks, staff_verified: -1 } } })).toBe(false)
    expect(isReport({ ...report, snapshot: { ...snapshot, focus: [{ ...snapshot.focus[0], status_at_period_end: 'weak' }] } })).toBe(false)
    expect(isReport({ ...report, snapshot: null })).toBe(false)
    expect(isReport({ ...report, cadence: 'weekly' })).toBe(false)
  })
  it('每日反馈：错题订正与自愿活动', () => {
    const mistake = { case_id: 'case_1', homework_title: '数学练习册', page: 2, question: '计算 -3 + 5', verdict: '答错', progress: 'needs_correction', correction: '还没订正' }
    const daily = { ...report, cadence: 'daily', period_start: '2026-10-17', period_end: '2026-10-18', supersedes_id: null,
      snapshot: { ...snapshot, mistakes: [mistake], activity_kinds: { preview: 1, variant: 0 } } }
    expect(isReport(daily)).toBe(true)
    expect(isReport({ ...daily, snapshot: { ...daily.snapshot, mistakes: [{ ...mistake, page: -1 }] } })).toBe(false)
    expect(isReport({ ...daily, snapshot: { ...daily.snapshot, activity_kinds: { preview: 'one', variant: 0 } } })).toBe(false)
  })
  it('家长链接只接受 /r/{token}', () => {
    const base = { id: 'share_1', report_id: 'report_1', created_by: 'staff_a', created_at: at, expires_at: '2026-11-15T08:00:00+00:00', revoked_at: null, view_count: 0, last_viewed_at: null }
    const token = 'A'.repeat(43)
    expect(isCreatedShareLink({ ...base, token, path: `/r/${token}` })).toBe(true)
    expect(isCreatedShareLink({ ...base, token, path: `https://evil.test/r/${token}` })).toBe(false)
    expect(isCreatedShareLink({ ...base, token: 'a b', path: '/r/a b' })).toBe(false)
  })
  it('选做窗口闸门', () => {
    const gate = { open: false, reasons: ['school_tasks_not_confirmed'], school_tasks_complete: { value: null, source: 'system', detail: 'no_homework_today' },
      has_capacity: { value: true, source: 'staff', actor: 'staff_a', at }, extra_minutes: 10, remaining_minutes: 10, window_revision: 1, checked_at: at }
    expect(isGate(gate)).toBe(true)
    expect(isGate({ ...gate, reasons: ['tired'] })).toBe(false)
    expect(isGate({ ...gate, school_tasks_complete: { value: true, source: 'parent' } })).toBe(false)
  })
})

describe('状态文字', () => {
  it('旧记录不猜老师来源，AI 与老师记录按来源计数', () => {
    expect(evidenceSourceLabel(undefined)).toBe('来源未标注')
    expect(evidenceSourceLabel('model')).toBe('AI 初判，尚未经老师核实')
    expect(evidenceSourceCounts({})).toBe('作答来源尚未分列')
    expect(evidenceSourceCounts({ model_evidence_count: 2, staff_verified_evidence_count: 1 })).toBe('AI 初判 2 次 · 老师核实 1 次')
  })
  it('知识证据与每日/半月预览不会把 AI 记录写成老师判断', () => {
    const item: unknown = { ...evidence, source_role: 'model' }
    if (!isEvidence(item)) throw new Error('Invalid evidence fixture')
    const evidenceHtml = renderToStaticMarkup(createElement(EvidenceRow, { item }))
    expect(evidenceHtml).toContain('AI 初判，尚未经老师核实')
    expect(evidenceHtml).not.toContain('老师已核实')
    const body: unknown = { ...report, cadence: 'daily', snapshot: {
      ...snapshot,
      school_tasks: { ...snapshot.school_tasks, staff_verified: 0, awaiting_verification: 1,
        verdicts: { correct: 0, incorrect: 0, partially_correct: 0, unanswered: 0 },
        ai_judged: 1, ai_corrected_pending: 1, ai_verdicts: { correct: 0, incorrect: 1, partially_correct: 0, unanswered: 0 } },
      focus: [{ ...snapshot.focus[0], model_evidence_count: 1, staff_verified_evidence_count: 0,
        cards: [{ ...snapshot.focus[0].cards[0], source_role: 'model', certainty: 'AI 初判，尚未经老师核实', staff_explanation: null }] }],
      mistakes: [{ case_id: 'case_a', homework_title: null, page: 1, question: '1 + 1 = ?', verdict: '答错',
        progress: 'needs_correction', correction: '还没订正', source_role: 'model', certainty: 'AI 初判，尚未经老师核实' }],
    } }
    if (!isReport(body) || !body.snapshot) throw new Error('Invalid report fixture')
    const dailyHtml = renderToStaticMarkup(createElement(DailySnapshotView, { snap: body.snapshot }))
    const reportHtml = renderToStaticMarkup(createElement(SnapshotView, { snap: body.snapshot }))
    for (const html of [dailyHtml, reportHtml]) {
      expect(html).toContain('AI 初判，未经老师核实')
      expect(html).toContain('AI 初判 1 次')
      expect(html).toContain('老师核实 0 次')
      expect(html).toContain('订正 AI 初判通过')
      expect(html).not.toContain('次已核实作答中出错')
    }
    expect(dailyHtml).not.toContain('<dt>老师判断</dt>')
  })
  it('四种学习状态都有中文且不含百分比', () => {
    expect(statusLabels).toEqual({ needs_help_now: '当前需要帮助', consolidating: '正在巩固', stable_for_now: '阶段性较稳', no_judgment: '暂不判断' })
    expect(Object.values(statusLabels).join('')).not.toMatch(/[%％\d]/)
    expect(statusLabel('unknown_value')).toBe('暂不判断')
  })
  it('摘要状态：完成但证据不足时如实显示', () => {
    const run = (status: AgentRun['status'], output: AgentRun['output'] = null): AgentRun => ({ id: 'agentrun_1', status, context: {}, output, failure_code: null, created_at: at, completed_at: null })
    expect(runView(run('completed', { status: 'needs_evidence', summary: '尚无', claims: [], next_action: '' }))).toBe('needs_evidence')
    expect(runView(run('completed', { status: 'ready', summary: 'x', claims: [], next_action: '' }))).toBe('ready')
    expect(runView(run('stale', { status: 'ready', summary: 'x', claims: [], next_action: '' }))).toBe('stale')
    expect(runView(run('queued'))).toBe('queued')
  })
})

describe('半月周期（北京时间）', () => {
  const utc = (value: string) => Date.parse(value)
  it('上一个已结束的半月', () => {
    expect(lastFinishedPeriod(utc('2026-10-15T16:30:00Z'))).toBe('2026-10-01')
    expect(lastFinishedPeriod(utc('2026-10-15T15:59:00Z'))).toBe('2026-09-16')
    expect(lastFinishedPeriod(utc('2026-11-01T01:00:00Z'))).toBe('2026-10-16')
    expect(lastFinishedPeriod(utc('2026-10-31T15:59:00Z'))).toBe('2026-10-01')
    expect(lastFinishedPeriod(utc('2027-01-05T00:00:00Z'))).toBe('2026-12-16')
  })
  it('列出最近几期，跨年；当前周期不在其中', () => {
    expect(finishedPeriods(utc('2026-01-20T00:00:00Z'), 4)).toEqual(['2026-01-01', '2025-12-16', '2025-12-01', '2025-11-16'])
  })
  it('窗口左闭右开，结束于北京时间零点', () => {
    expect(periodFromStart('2026-12-16')).toEqual({ start: '2026-12-16', endExclusive: '2027-01-01' })
    expect(periodFromStart('2026-10-05')).toBeNull()
    expect(periodFinished('2026-10-01', utc('2026-10-15T15:59:59Z'))).toBe(false)
    expect(periodFinished('2026-10-01', utc('2026-10-15T16:00:00Z'))).toBe(true)
    expect(periodLabel('2028-02-16')).toBe('2028-02-16 至 2028-02-29')
    expect(periodLabel('2026-10-01', '2026-10-16')).toBe('2026-10-01 至 2026-10-15')
  })
})

describe('每日反馈', () => {
  const utc = (value: string) => Date.parse(value)
  it('按北京时间列出今天和之前几天，跨月', () => {
    expect(recentDays(utc('2026-10-31T16:30:00Z'), 3)).toEqual(['2026-11-01', '2026-10-31', '2026-10-30'])
    expect(dayLabel('2026-11-01', utc('2026-10-31T16:30:00Z'))).toBe('11月1日（今天）')
    expect(dayLabel('2026-10-31', utc('2026-10-31T16:30:00Z'))).toBe('10月31日（昨天）')
    expect(dayLabel('2026-10-29', utc('2026-10-31T16:30:00Z'))).toBe('10月29日')
  })
  it('发布、复制、老师确认发送和家长打开分开显示', () => {
    const published = (delivery: { links: number; copied: boolean; sent_confirmed_at: string | null; opened_at: string | null } | null) => ({ status: 'published', supersedes_id: null, delivery })
    const none = { links: 0, copied: false, sent_confirmed_at: null, opened_at: null }
    expect(deliveryLabel(null)[0]).toBe('今天没有记录')
    expect(deliveryLabel({ status: 'draft', supersedes_id: null, delivery: null })[0]).toBe('待核对发布')
    expect(deliveryLabel({ status: 'draft', supersedes_id: 'report_1', delivery: null })[0]).toBe('有更新待发布')
    expect(deliveryLabel(published(none))[0]).toBe('已发布，未发给家长')
    expect(deliveryLabel(published({ ...none, links: 1 }))[0]).toBe('已生成链接，未复制')
    expect(deliveryLabel(published({ ...none, links: 1, copied: true }))[0]).toBe('已复制，待确认发送')
    expect(deliveryLabel(published({ ...none, links: 1, copied: true, sent_confirmed_at: at }))[0]).toBe('老师已确认发送')
    expect(deliveryLabel(published({ ...none, links: 1, opened_at: at }))[0]).toBe('家长已打开')
  })
  it('接受带发送记录的链接与列表，拒绝格式错误的记录', () => {
    const base = { id: 'share_1', report_id: 'report_1', created_at: at, expires_at: at, revoked_at: null, view_count: 0, last_viewed_at: null }
    expect(isShareLink(base)).toBe(true)
    expect(isShareLink({ ...base, message_copied_at: at, sent_confirmed_at: at, sent_confirmed_by: 'staff_a', sent_note: '已发妈妈微信' })).toBe(true)
    expect(isShareLink({ ...base, sent_confirmed_at: 'yesterday' })).toBe(false)
    const queued = { id: 'report_1', student_id: 'student_a', cadence: 'daily', period_start: '2026-10-17', period_end: '2026-10-18', version: 1, status: 'published',
      snapshot: null, staff_note: null, next_step: null, correction_note: null, supersedes_id: null, published_at: at, revision: 2, regenerated_reason: null,
      published_by: 'staff_a', source_digest: 'a'.repeat(64), created_at: at, updated_at: at, focus_count: 0, mistake_count: 0, sufficient: true, student_name: '小明' }
    expect(isReportQueue({ items: [{ ...queued, delivery: { links: 1, copied: true, sent_confirmed_at: null, opened_at: null } }], next_offset: null })).toBe(true)
    expect(isReportQueue({ items: [{ ...queued, delivery: null }], next_offset: null })).toBe(true)
    expect(isReportQueue({ items: [{ ...queued, delivery: { links: -1, copied: true, sent_confirmed_at: null, opened_at: null } }], next_offset: null })).toBe(false)
  })
  it('发给家长的消息只含姓名、日期和链接', () => {
    expect(dailyMessage('小明', '2026-10-17', 'https://xueban.example.cn/r/abc')).toBe(
      '【学伴】小明 10月17日的学习反馈已整理好（今天交的作业、错题订正情况和老师的话），请点开查看：https://xueban.example.cn/r/abc')
  })
})

describe('家长链接', () => {
  const token = 'abcDEF123_-'.repeat(4)
  it('用服务地址拼出绝对链接', () => {
    expect(shareUrl('https://xueban.example.cn', `/r/${token}`)).toBe(`https://xueban.example.cn/r/${token}`)
    expect(shareUrl('http://127.0.0.1:8000/', `/r/${token}`)).toBe(`http://127.0.0.1:8000/r/${token}`)
  })
  it('拒绝非 /r/ 路径或外部地址', () => {
    for (const bad of [`//evil.test/r/${token}`, `/v1/report-links/${token}`, `/r/${token}?x=1`, '/r/short']) expect(() => shareUrl('https://xueban.example.cn', bad)).toThrow()
    expect(() => shareUrl('javascript:alert(1)', `/r/${token}`)).toThrow()
  })
  it('有效、已撤销、已过期', () => {
    const now = Date.parse('2026-10-20T00:00:00Z')
    expect(shareState({ revoked_at: null, expires_at: '2026-11-01T00:00:00Z' }, now)).toBe('active')
    expect(shareState({ revoked_at: '2026-10-19T00:00:00Z', expires_at: '2026-11-01T00:00:00Z' }, now)).toBe('revoked')
    expect(shareState({ revoked_at: null, expires_at: '2026-10-19T00:00:00Z' }, now)).toBe('expired')
  })
})

describe('写入前的本地规则', () => {
  it('一道题只有一个主要知识点', () => {
    const links = [link(), link({ id: 'qkl_2', knowledge_id: 'kp-compare', knowledge_name: '比较', role: 'secondary' })]
    expect(primaryConflict(links, 'kp-compare', 'primary')).toContain('有理数加减')
    expect(primaryConflict(links, 'kp-compare', 'secondary')).toBeNull()
    expect(primaryConflict(links, 'kp-rational-add-sub', 'primary')).toBeNull()
    expect(primaryConflict(links, 'kp-new', 'primary', 'kp-rational-add-sub')).toBeNull()
    expect(primaryConflict([link({ status: 'rejected' })], 'kp-new', 'primary')).toBeNull()
  })
  it('看过提示的作答不能标为独立', () => {
    const attempt = sampleWorkspace.attempts[0]
    expect(helpSeen(attempt, [])).toBe(true)
    const clean = { ...attempt, record: { ...attempt.record, help_condition: 'none_reported' as const } }
    expect(helpSeen(clean, [])).toBe(false)
    expect(helpSeen(clean, sampleWorkspace.hints)).toBe(true)
  })
  it('记忆：学科困难必须引用有效证据，内容不超过 120 字', () => {
    const valid = new Set(['evidence_1'])
    expect(memoryIssue({ category: 'recurring_difficulty', content: '异号相加时符号易错', knowledge_id: null, evidence_ids: [] }, valid)).toContain('证据')
    expect(memoryIssue({ category: 'recurring_difficulty', content: '异号相加时符号易错', knowledge_id: null, evidence_ids: ['evidence_x'] }, valid)).toContain('有效')
    expect(memoryIssue({ category: 'recurring_difficulty', content: '异号相加时符号易错', knowledge_id: null, evidence_ids: ['evidence_1'] }, valid)).toBeNull()
    expect(memoryIssue({ category: 'support_agreement', content: '好'.repeat(121), knowledge_id: null, evidence_ids: [] }, valid)).toContain('120')
    expect(memoryIssue({ category: 'support_agreement', content: '先读题两遍', knowledge_id: null, evidence_ids: [] }, valid)).toBeNull()
  })
  it('已核实薄弱范围至少两条证据', () => {
    expect(diagnosisIssue('verified_weak_scope', '负数加法符号', ['e1'])).toContain('两条')
    expect(diagnosisIssue('suspected_cause', '可能是异号相加取符号', ['e1'])).toBeNull()
  })
  it('答案按逗号或换行拆分', () => {
    expect(parseAnswerValues(' 3/4 ，0.75\nB、 ')).toEqual(['3/4', '0.75', 'B'])
  })
})

describe('学情接口请求', () => {
  const ORIGIN = 'http://127.0.0.1:8000'
  const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } })
  async function signedIn(...responses: (Response | Error)[]) {
    const transport = vi.fn().mockResolvedValueOnce(response({ access_token: 'session-token', expires_at: '2099-01-01T00:00:00Z', must_change_password: false }))
    for (const item of responses) item instanceof Error ? transport.mockRejectedValueOnce(item) : transport.mockResolvedValueOnce(item)
    const api = new StaffApi(ORIGIN, { transport: transport as unknown as typeof fetch })
    await api.login('teacher.a', 'synthetic-password-123')
    return { api, learning: new LearningApi(api), transport }
  }
  const gate = { open: true, reasons: [], school_tasks_complete: { value: true, source: 'staff', actor: 'staff_a', at, detail: 'staff_confirmed' }, has_capacity: { value: true, source: 'staff', actor: 'staff_a', at },
    extra_minutes: 10, remaining_minutes: 10, window_revision: 1, checked_at: at }

  it('PUT 带幂等键；结果未知时用原方法、原键重试并按手写校验', async () => {
    const { api, learning, transport } = await signedIn(new TypeError('network'), response(gate))
    const body = { expected_revision: 0, capacity_observed: true, extra_minutes: 10, school_tasks_confirmed: true }
    await expect(learning.setWindow('student_a', body)).rejects.toMatchObject({ status: 0 })
    expect(api.hasPendingWrite()).toBe(true)
    const { value } = await api.retryPendingWrite()
    expect(value).toEqual(gate)
    const [first, second] = [transport.mock.calls[1], transport.mock.calls[2]]
    expect(first[0]).toBe(`${ORIGIN}/v1/students/student_a/optional-window`)
    expect([first[1].method, second[1].method]).toEqual(['PUT', 'PUT'])
    expect(second[1].headers['Idempotency-Key']).toBe(first[1].headers['Idempotency-Key'])
    expect(JSON.parse(second[1].body)).toEqual(body)
  })
  it('报告编辑用 PATCH；不符合校验的成功响应视为 502', async () => {
    const { learning, transport } = await signedIn(response({ ...report, revision: 4 }), response({ ...report, snapshot: { focus: 'x' } }))
    await learning.editReport('report_1', { expected_revision: 3, staff_note: '认真', next_step: null })
    expect(transport.mock.calls[1][1].method).toBe('PATCH')
    await expect(learning.report('report_1')).rejects.toMatchObject({ status: 502 })
  })
  it('最新版目录待审核时空图谱是正常响应，不转为 502', async () => {
    const body = { student_id: 'student_a', graph_version: null, graph_review_status: 'not_ready_latest_edition',
      projection: { state_version: 0, projected_at: null, pending: false }, states: [], chapters: [], notice: '目录待审核。' }
    const { learning } = await signedIn(response(body))
    await expect(learning.knowledge('student_a')).resolves.toEqual(body)
  })
  it('每日反馈按日期取队列，生成时带 cadence', async () => {
    const queued = { ...report, cadence: 'daily', snapshot: null, focus_count: 0, mistake_count: 2, sufficient: false, student_name: '合成学生' }
    const daily = { ...report, cadence: 'daily', period_start: '2026-10-17', period_end: '2026-10-18' }
    const { learning, transport } = await signedIn(response({ items: [queued], next_offset: null }), response(daily))
    const queue = await learning.reportQueue('draft', undefined, '2026-10-17')
    expect(queue.items[0].mistake_count).toBe(2)
    expect(transport.mock.calls[1][0]).toBe(`${ORIGIN}/v1/reports?status=draft&cadence=daily&period_start=2026-10-17&limit=200`)
    await learning.generateDaily('student_a', '2026-10-17')
    expect(JSON.parse(transport.mock.calls[2][1].body)).toEqual({ period_start: '2026-10-17', cadence: 'daily' })
    expect(() => learning.generateDaily('student_a', '10/17')).toThrow()
    expect(() => learning.reportQueue('draft', undefined, '2026-10-17&x=1')).toThrow()
  })
  it('本地拒绝不合规的周期起点和额外时间，不发请求', async () => {
    const { learning, transport } = await signedIn()
    expect(() => learning.generateReport('student_a', '2026-10-05')).toThrow()
    expect(() => learning.setWindow('student_a', { expected_revision: 0, capacity_observed: true, extra_minutes: 90, school_tasks_confirmed: null })).toThrow()
    expect(transport).toHaveBeenCalledTimes(1)
  })
})

describe('practice generation status', () => {
  const base = { job_id: 'ljob_1', knowledge_id: 'kp-rational-add-sub', failure_code: null, candidate_item_ids: [] as string[] }
  it('accepts only known statuses and identifier lists', () => {
    expect(isGenerationStatus({ ...base, status: 'running' })).toBe(true)
    expect(isGenerationStatus({ ...base, status: 'done' })).toBe(false)
    expect(isGenerationStatus({ ...base, status: 'succeeded', candidate_item_ids: ['not an id'] })).toBe(false)
  })
  it('tells staff what happened instead of a silent success', () => {
    expect(generationText({ ...base, status: 'running' }, true)).toContain('正在生成')
    expect(generationText({ ...base, status: 'succeeded', candidate_item_ids: ['item_a', 'item_b'] }, false)).toContain('2 道')
    expect(generationText({ ...base, status: 'failed', failure_code: 'provider_disabled' }, false)).toContain('模型服务未开启')
    expect(generationText(null, false)).toContain('稍后刷新')
  })
})

const mappingCoverage = {
  scope: 'current_reliable_errors_in_selected_graph' as const, time_basis: 'current_attempt_occurred_at' as const,
  graph_version: 'graph-test', window: { start: '2026-10-15T16:00:00Z', end_exclusive: '2026-10-31T16:00:00Z' }, available: true,
  total: 2, linked: 1, unlinked: 1, model_count: 2, staff_count: 0, truncated: false,
  notice: '当前教材内的当前作答，不含其他学科与旧版作业。',
  items: [{ case_id: 'case-gap', question_id: 'question-gap', homework_id: 'homework-gap', label: '1', subject: '数学',
    stem_preview: '18 - 7 = ____', source_role: 'model' as const, occurred_at: at }],
}

describe('half-month coverage snapshot', () => {
  it('keeps older reports valid and does not pretend an absent field is zero', () => {
    expect(isReport(report)).toBe(true)
    expect(renderToStaticMarkup(createElement(SnapshotView, { snap: report.snapshot as ReportSnapshot }))).not.toContain('尚未关联知识点')
    const body = { ...report, snapshot: { ...snapshot, mapping_coverage: mappingCoverage } }
    expect(isReport(body)).toBe(true)
    const html = renderToStaticMarkup(createElement(SnapshotView, { snap: body.snapshot as ReportSnapshot }))
    for (const text of ['还有 1 道错题', '生成这份报告时', '18 - 7', 'AI 初判，尚未经老师核实', '后续订正请以新版本为准']) expect(html).toContain(text)
    expect(html).not.toContain('归类尚未完成')
    expect(isReport({ ...body, snapshot: { ...body.snapshot, mapping_coverage: { ...mappingCoverage, total: 9 } } })).toBe(false)
  })
})


describe('report judgment wording', () => {
  const known = { ...snapshot.school_tasks, questions: 9, staff_verified: 1, awaiting_verification: 6, not_judged: 2,
    ai_judged: 4, ai_verdicts: { correct: 2, incorrect: 1, partially_correct: 1, unanswered: 0 } }
  const renderBoth = (tasks: ReportSnapshot['school_tasks']) => [DailySnapshotView, SnapshotView].map(View =>
    renderToStaticMarkup(createElement(View, { snap: { ...snapshot, school_tasks: tasks } as ReportSnapshot })))
  it('separates usable AI judgments from unfinished reads without changing stored counts', () => {
    const before = structuredClone(known)
    expect(reportJudgmentCounts(known)).toEqual({ ai: 4, unfinished: 4, unclassified: 0 })
    for (const html of renderBoth(known)) {
      expect(html).toContain('<dt>AI 初判，未经老师核实</dt><dd>4 道')
      expect(html).toContain('<dt>尚未形成可用判定</dt><dd>4 道')
      expect(html).not.toContain('待老师核实')
      expect(html).not.toContain('AI 候选待确认')
    }
    expect(known).toEqual(before)
  })
  it.each([
    { ...known, ai_judged: undefined, ai_verdicts: undefined },
    { ...known, ai_judged: undefined }, { ...known, ai_verdicts: undefined },
    { ...known, ai_judged: 7 }, { ...known, ai_judged: 3 },
  ])('keeps old or inconsistent snapshots unclassified, never guesses AI zero', tasks => {
    const before = structuredClone(tasks)
    expect(reportJudgmentCounts(tasks)).toEqual({ ai: null, unfinished: 2, unclassified: 6 })
    for (const html of renderBoth(tasks)) {
      expect(html).toContain('<dt>未分列的未核实记录</dt><dd>6 道')
      expect(html).toContain('这份记录未可靠分列有效 AI 初判与未完成识读')
      expect(html).toContain('<dt>尚无判定（已记录）</dt><dd>2 道')
      expect(html).not.toContain('<dt>AI 初判，未经老师核实</dt>')
      expect(html).not.toContain('待老师核实')
    }
    expect(tasks).toEqual(before)
  })
  it('distinguishes an explicit zero AI count from an absent field', () => {
    const tasks = { ...known, ai_judged: 0, ai_verdicts: { correct: 0, incorrect: 0, partially_correct: 0, unanswered: 0 } }
    expect(reportJudgmentCounts(tasks)).toEqual({ ai: 0, unfinished: 8, unclassified: 0 })
    expect(renderBoth(tasks)[0]).toContain('<dt>AI 初判，未经老师核实</dt><dd>0 道')
  })

})
