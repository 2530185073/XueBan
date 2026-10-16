import { describe, expect, it, vi } from 'vitest'
import type { AttemptRead, HelpRequest, Job } from './api/contracts.generated'
import { aspectMatches, insideImage, MIN_REGION_SIDE, regionFromDrag, toOriginal } from './geometry'
import { pollJob } from './jobs'
import { activeHelpByCase, applyPage, emptyQueue, filterHelp, helpId, mergeById, orderHelp } from './queue'
import { sampleHelp, sampleQuestions, sampleWorkspace } from './sample'
import { attemptChain, buildRepair, buildReview, editBlock, focusHelp, newCaseIds, repairIssue, reviewBlock, reviewIssue, type CaseWorkspace } from './workspace'
import { matchesContract } from './api/validation'

const size = { width: 2000, height: 3000 }
const box = { left: 100, top: 50, width: 500, height: 750 }

describe('框选：显示坐标映射到原图像素', () => {
  it('按显示尺寸线性换算，并夹在图片范围内', () => {
    expect(toOriginal(100, 50, box, size)).toEqual({ x: 0, y: 0 })
    expect(toOriginal(350, 425, box, size)).toEqual({ x: 1000, y: 1500 })
    expect(toOriginal(600, 800, box, size)).toEqual({ x: 2000, y: 3000 })
    expect(toOriginal(-40, 2000, box, size)).toEqual({ x: 0, y: 3000 })
  })
  it('任意拖动方向得到整数原图像素矩形，且不越界', () => {
    const start = toOriginal(225.3, 175.9, box, size), end = toOriginal(150.1, 100.2, box, size)
    const region = regionFromDrag('asset_one', start, end, size)!
    expect(region).toEqual({ asset_id: 'asset_one', x: 200, y: 200, width: 302, height: 304, coordinate_space: 'original_pixels' })
    expect(Number.isInteger(region.x + region.y + region.width + region.height)).toBe(true)
    expect(insideImage(region, size)).toBe(true)
    const edge = regionFromDrag('asset_one', { x: 1990.4, y: 2990.7 }, { x: 2400, y: 3500 }, size)!
    expect(edge.x + edge.width).toBe(2000)
    expect(edge.y + edge.height).toBe(3000)
    expect(matchesContract('data', 'Region', edge)).toBe(true)
  })
  it('误触的极小矩形被忽略', () => {
    expect(regionFromDrag('asset_one', { x: 10, y: 10 }, { x: 10 + MIN_REGION_SIDE - 1, y: 400 }, size)).toBeNull()
  })
  it('显示比例与记录尺寸不一致时不能框选', () => {
    expect(aspectMatches({ width: 1000, height: 1500 }, size)).toBe(true)
    expect(aspectMatches({ width: 3000, height: 2000 }, size)).toBe(false)
    expect(aspectMatches({ width: 0, height: 0 }, size)).toBe(false)
  })
})

const help = (id: string, overrides: Partial<HelpRequest> = {}): HelpRequest => ({ ...sampleHelp, id, ...overrides })

describe('队列合并与排序', () => {
  it('刷新替换旧副本、保持原顺序，新记录追加且不重复', () => {
    const merged = mergeById([help('help_a'), help('help_b')], [help('help_b', { status: 'claimed' }), help('help_c'), help('help_c')], helpId)
    expect(merged.map(item => item.id)).toEqual(['help_a', 'help_b', 'help_c'])
    expect(merged[1].status).toBe('claimed')
  })
  it('首页刷新整体替换；加载更多则合并并更新游标', () => {
    const first = applyPage(emptyQueue<HelpRequest>(), { items: [help('help_a')], next_cursor: 'help_a' }, helpId, false)
    const more = applyPage(first, { items: [help('help_a'), help('help_b')], next_cursor: null }, helpId, true)
    expect(more).toEqual({ items: [help('help_a'), help('help_b')], cursor: null, loaded: true })
    expect(applyPage(more, { items: [help('help_c')], next_cursor: null }, helpId, false).items.map(helpId)).toEqual(['help_c'])
  })
  it('申请重查排在最前，其余按等待时间；筛选可按学生姓名', () => {
    const items = [help('help_new', { created_at: '2026-10-16T09:00:00Z' }), help('help_old', { created_at: '2026-10-16T08:00:00Z' }), help('help_appeal', { kind: 'appeal', created_at: '2026-10-16T10:00:00Z', case_id: 'case_two' })]
    expect(orderHelp(items).map(helpId)).toEqual(['help_appeal', 'help_old', 'help_new'])
    expect(filterHelp(items, 'all', '小林', new Map([['case_two', '合成小林']])).map(helpId)).toEqual(['help_appeal'])
    expect(filterHelp([...items, help('help_claimed', { status: 'claimed' })], 'claimed', '', new Map()).map(helpId)).toEqual(['help_claimed'])
  })
  it('只把等待或处理中的求助标记到对应题目', () => {
    const map = activeHelpByCase([help('help_done', { status: 'resolved' }), help('help_wait', { case_id: 'case_x' })])
    expect([...map.keys()]).toEqual(['case_x'])
  })
})

const attempt = (id: string, parent: string | null, occurred: string): AttemptRead => ({ ...sampleWorkspace.attempts[0], record: { ...sampleWorkspace.attempts[0].record, id, parent_attempt_id: parent, occurred_at: occurred } })
const workspace = (overrides: Partial<CaseWorkspace> = {}): CaseWorkspace => ({ ...sampleWorkspace, ...overrides })

describe('作答链、焦点求助与处理门槛', () => {
  it('从原始作答沿父链排列订正，并标记当前作答', () => {
    const chain = attemptChain([attempt('attempt_c', 'attempt_b', '2026-10-16T10:00:00Z'), attempt('attempt_a', null, '2026-10-16T08:00:00Z'), attempt('attempt_b', 'attempt_a', '2026-10-16T09:00:00Z')], 'attempt_c')
    expect(chain.map(entry => [entry.label, entry.parentLabel, entry.current])).toEqual([['原始作答', null, false], ['第 1 次订正', '原始作答', false], ['第 2 次订正', '第 1 次订正', true]])
  })
  it('没有选定求助时，聚焦最新的进行中求助', () => {
    const ws = workspace({ helps: [help('help_old', { created_at: '2026-10-16T08:00:00Z' }), help('help_done', { status: 'resolved', created_at: '2026-10-16T11:00:00Z' }), help('help_new', { created_at: '2026-10-16T09:00:00Z' })] })
    expect(focusHelp(ws, null)?.id).toBe('help_new')
    expect(focusHelp(ws, 'help_done')?.id).toBe('help_done')
  })
  it('待接手或他人接手的求助、已被替代的题都不能直接修改', () => {
    const live = (ws: CaseWorkspace, focus: HelpRequest | null) => editBlock(ws, focus, 'staff_me', false)
    expect(live(workspace(), help('help_w'))).toContain('先接手')
    expect(live(workspace(), help('help_c', { status: 'claimed', claimed_by: 'staff_other' }))).toContain('其他老师')
    expect(live(workspace(), help('help_c', { status: 'claimed', claimed_by: 'staff_me' }))).toBeNull()
    expect(live(workspace(), null)).toBeNull()
    expect(live(workspace({ correction: { ...sampleWorkspace.correction, lifecycle: 'superseded' } }), null)).toContain('替代')
    expect(editBlock(workspace(), null, 'staff_me', true)).toContain('合成')
  })
  it('判断依据版本过期时不能复核', () => {
    expect(reviewBlock(workspace())).toBeNull()
    expect(reviewBlock(workspace({ question: { ...sampleWorkspace.question, current_revision: 2 } }))).toContain('版本')
    expect(reviewBlock(workspace({ decision: { ...sampleWorkspace.decision!, currently_effective: false } }))).toContain('版本')
  })
  it('复核：确认沿用候选结论，人工初判需选结果，载荷符合契约', () => {
    const ws = workspace()
    const form = { action: 'confirm' as const, verdict: '' as const, reason: '对照原图', basis: '第二项漏乘', regions: ws.question.record.regions }
    expect(reviewIssue(ws, form)).toBeNull()
    const payload = buildReview(ws, form)
    expect(payload).toMatchObject({ expected_revision: 3, action: 'confirm', verdict: 'incorrect', target_decision_id: 'decision_synthetic', attempt_id: 'attempt_synthetic' })
    expect(matchesContract('requests', 'SubmitReview', payload)).toBe(true)
    expect(reviewIssue(workspace({ decision: null }), { ...form, action: 'manual_initial' })).toContain('核实结果')
    const manual = buildReview(workspace({ decision: null }), { ...form, action: 'manual_initial', verdict: 'correct' })
    expect(manual).toMatchObject({ target_decision_id: null, target_decision_revision: null, verdict: 'correct' })
    expect(matchesContract('requests', 'SubmitReview', manual)).toBe(true)
    const unsure = workspace({ decision: { ...sampleWorkspace.decision!, record: { ...sampleWorkspace.decision!.record, verdict: 'unable_to_judge' } } })
    expect(reviewIssue(unsure, form)).toContain('更正')
    expect(matchesContract('requests', 'SubmitReview', buildReview(ws, { ...form, action: 'leave_unresolved' }))).toBe(true)
  })
  it('补题/拆合：每道新题至少一个区域，选中的旧题带各自版本', () => {
    const region = sampleQuestions[1].record.regions[0]
    expect(repairIssue([{ regions: [], stem: '' }], '错切')).toContain('至少')
    expect(repairIssue([{ regions: [region], stem: '' }], ' ')).toContain('原因')
    const payload = buildRepair(sampleWorkspace.homework, [sampleQuestions[0]], [{ regions: [region], stem: '  ' }, { regions: [region], stem: '化简：5y − 2y。' }], ' 拆分 ')
    expect(payload).toEqual({ expected_revision: 4, supersede_questions: [{ question_id: 'question_synthetic', expected_revision: 1 }], replacement_regions: [{ regions: [region], stem_text: null }, { regions: [region], stem_text: '化简：5y − 2y。' }], reason: '拆分' })
    expect(matchesContract('requests', 'RepairLayout', payload)).toBe(true)
    expect(newCaseIds(['case_a'], { ...sampleWorkspace.homework, case_ids: ['case_a', 'case_b', 'case_c'] })).toEqual(['case_b', 'case_c'])
  })
})

describe('补题任务轮询', () => {
  const job = (status: Job['status'], revision: number): Job => ({ id: 'job_repair', revision, kind: 'repair_layout', status, created_at: '2026-10-16T08:35:00Z', updated_at: '2026-10-16T08:35:00Z', retry_after_seconds: 1, failure: null, result: null })
  it('轮询到终态为止，并逐次回报进度', async () => {
    const read = vi.fn().mockResolvedValueOnce(job('running', 2)).mockResolvedValueOnce(job('retryable_failed', 3)).mockResolvedValueOnce(job('succeeded', 4))
    const updates: string[] = []
    const result = await pollJob(read, job('queued', 1), { sleep: async () => undefined, onUpdate: next => updates.push(next.status) })
    expect(result.status).toBe('succeeded')
    expect(updates).toEqual(['running', 'retryable_failed', 'succeeded'])
  })
  it('超时返回最后一次状态，不伪造完成', async () => {
    let now = 0
    const read = vi.fn().mockResolvedValue(job('running', 2))
    const result = await pollJob(read, job('queued', 1), { sleep: async ms => { now += ms }, now: () => now, timeoutMs: 5000 })
    expect(result.status).toBe('running')
  })
  it('旧版本的响应不会覆盖较新状态', async () => {
    const read = vi.fn().mockResolvedValueOnce(job('queued', 1)).mockResolvedValueOnce(job('failed', 3))
    const updates: string[] = []
    await pollJob(read, job('running', 2), { sleep: async () => undefined, onUpdate: next => updates.push(next.status) })
    expect(updates).toEqual(['failed'])
  })
})
