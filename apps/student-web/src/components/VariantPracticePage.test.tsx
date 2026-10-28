import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'
import type { VariantErrorGroup, VariantGeneration } from '../core'
import type { Workspace } from '../useStudentWorkspace'
import { variantOverview } from '../core/learning.fixtures'
import { generationCopy, generationPending, VariantGenerationCard, VariantGroupCard, VariantPracticePage } from './VariantPracticePage'

const group = variantOverview.groups[0] as VariantErrorGroup
const job: VariantGeneration = { job_id: 'generation_one', student_id: 'student_one', source_case_id: 'case_one', graph_version: 'g1', knowledge_id: group.knowledge_id, question_type: 'calculation', status: 'queued', item_id: null, failure_code: null, notice: '合成测试' }
const render = (value: VariantErrorGroup, generation?: VariantGeneration) => renderToStaticMarkup(<VariantGroupCard group={value} generation={generation} disabled={false} busy={false} onStart={() => {}} />)
const renderJob = (value: VariantGeneration) => renderToStaticMarkup(<VariantGenerationCard job={value} disabled={false} busy={false} onOpen={() => {}} onRetry={() => {}} />)
const invalidNotice = '这道练习的参考答案未通过计算核验，请重新生成；原作答保留，但不计入练习反馈。'

describe('voluntary generation availability', () => {
  it.each([null, 'session_one'])('offers explicit regeneration for an invalid reference with session %s', session_id => {
    const invalid: VariantGeneration = { ...job, status: 'superseded', failure_code: 'verification_failed', session_id, notice: invalidNotice }
    expect(generationCopy(invalid)).toBe(invalidNotice)
    for (const html of [renderJob(invalid), render(group, invalid)]) {
      expect(html).toContain(invalidNotice); expect(html).toMatch(/>\s*重新生成</)
      expect(html).not.toContain('原题或教材记录已更新'); expect(html).not.toContain('查看这次作答')
      expect(html).not.toContain('disabled=""'); expect(html).not.toContain('spin')
    }
    const retrying = renderToStaticMarkup(<VariantGenerationCard job={invalid} busy disabled generating onOpen={() => {}} onRetry={() => {}} />)
    expect(retrying).toContain('正在围绕原题'); expect(retrying).not.toContain(invalidNotice)
    expect(retrying).not.toMatch(/>\s*重新生成<\/button>/)
  })
  it('keeps invalid-reference fallback specific and escapes the backend notice', () => {
    const invalid: VariantGeneration = { ...job, status: 'superseded', failure_code: 'verification_failed', notice: ' \n ' }
    expect(generationCopy(invalid)).toBe(invalidNotice)
    const html = renderJob({ ...invalid, notice: '<img src=x onerror="alert(1)">请重新生成' })
    expect(html).toContain('&lt;img'); expect(html).not.toContain('<img')
    expect(renderJob({ ...invalid, failure_code: 'stale_source' })).toContain('原题或教材记录已更新')
    expect(renderJob({ ...invalid, failure_code: 'stale_source' })).not.toMatch(/>\s*重新生成<\/button>/)
  })
  it('does not regenerate an invalid historical item while rendering or reopening the page', () => {
    const generateVariant = vi.fn(), onStarted = vi.fn()
    const workspace = { client: { generateVariant }, studentId: 'student_one', recordVersion: 0, busy: '', report: vi.fn() } as unknown as Workspace
    const invalid: VariantGeneration = { ...job, status: 'superseded', failure_code: 'verification_failed', session_id: 'session_one', notice: invalidNotice }
    for (let i = 0; i < 2; i++) {
      const html = renderToStaticMarkup(<VariantPracticePage workspace={workspace} initialGeneration={invalid} onStarted={onStarted} />)
      expect(html).toContain(invalidNotice); expect(html).toMatch(/>\s*重新生成<\/button>/)
    }
    expect(generateVariant).not.toHaveBeenCalled(); expect(onStarted).not.toHaveBeenCalled()
  })
  it('offers explicit generation with an empty bank, without claiming a job already runs', () => {
    const html = render({ ...group, available_items: 0 })
    expect(html).toContain('生成一道同题型'); expect(html).not.toContain('disabled=""')
    expect(html).not.toContain('正在准备'); expect(html).not.toContain('正在生成')
    expect(html).toContain('有 1 道题可以再看看'); expect(html).not.toContain('错过 1 次')
  })
  it('keeps another book separate even if item counts are stale', () => {
    const html = render({ ...group, available_items: 2, graph_version: null, curriculum_status: 'historical_unmatched' })
    expect(html).toContain('保留原教材作答记录'); expect(html).toContain('暂不用于当前教材的练习')
    expect(html).toContain('disabled=""'); expect(html).not.toContain('生成一道同题型')
  })
  it('can use an original question without inventing a knowledge association', () => {
    const html = render({ ...group, available_items: 0, graph_version: null, curriculum_status: 'current' })
    expect(html).toContain('未确认的关联不会计入知识图谱'); expect(html).not.toContain('disabled=""')
  })
  it('only displays progress from an actual nonterminal generation state', () => {
    for (const status of ['queued', 'generating', 'verifying'] as const) {
      const current = { ...job, status }
      expect(generationPending(current)).toBe(true)
      expect(render(group, current)).toContain('disabled=""')
      expect(renderJob(current)).toContain('等待不占用作答时间')
    }
    for (const status of ['ready', 'failed', 'superseded'] as const) expect(generationPending({ ...job, status })).toBe(false)
    const failed = renderJob({ ...job, status: 'failed', failure_code: 'verification_failed' })
    expect(failed).toContain('未通过核验'); expect(failed).not.toContain('spin'); expect(failed).not.toContain('老师')
  })
  it('starts a ready item only after a separate explicit choice; consumed jobs open the original session', () => {
    expect(renderJob({ ...job, status: 'ready', item_id: 'item_one' })).toContain('开始这道题')
    const consumed = { ...job, status: 'ready' as const, item_id: 'item_one', session_id: 'session_one' }
    expect(renderJob(consumed)).toContain('查看这次作答'); expect(renderJob(consumed)).not.toContain('开始这道题')
    expect(render(group, consumed)).toContain('查看这次作答')
  })
  it('does not offer automatic retries for a changed source or unsupported question', () => {
    expect(renderJob({ ...job, status: 'superseded' })).not.toContain('再生成一次')
    expect(renderJob({ ...job, status: 'failed', failure_code: 'unsupported_source_type' })).not.toContain('再生成一次')
    expect(renderJob({ ...job, status: 'failed', failure_code: 'provider_unavailable' })).toContain('再生成一次')
    expect(generationCopy({ ...job, status: 'failed', failure_code: 'provider_unavailable' })).not.toContain('答错')
  })
  it.each([
    ['source_structure_invalid', '原题的填空位置或数量还不清楚，请先核对原题后再生成练习。'],
    ['candidate_structure_invalid', '生成题的填空位置或逐空答案没有对齐，本次未保存练习，可以稍后重试。'],
    ['reference_structure_invalid', '独立解答的逐空答案没有对齐，本次未保存练习，可以稍后重试。'],
  ])('shows the backend explanation for %s and clears it while retrying', (failure_code, notice) => {
    const failed = { ...job, status: 'failed' as const, failure_code, notice }
    expect(renderJob(failed)).toContain(notice)
    expect(renderJob(failed)).not.toContain('这次还没能准备好题目')
    const retrying = renderToStaticMarkup(<VariantGenerationCard job={failed} busy disabled generating onOpen={() => {}} onRetry={() => {}} />)
    expect(retrying).toContain('正在围绕原题'); expect(retrying).not.toContain(notice)
    expect(retrying).not.toContain('再生成一次')
  })
  it('keeps the fallback for empty notices and does not use a failed notice for pending or ready jobs', () => {
    const failed = { ...job, status: 'failed' as const, failure_code: 'source_structure_invalid', notice: ' \n ' }
    expect(renderJob(failed)).toContain('这次还没能准备好题目')
    for (const status of ['queued', 'generating', 'verifying', 'ready'] as const) {
      expect(renderJob({ ...failed, status, notice: '旧的结构错误提示' })).not.toContain('旧的结构错误提示')
    }
  })
  it('renders a structural failure notice as escaped text', () => {
    const html = renderJob({ ...job, status: 'failed', failure_code: 'source_structure_invalid', notice: '<img src=x onerror="alert(1)">请核对原题' })
    expect(html).toContain('&lt;img'); expect(html).toContain('请核对原题'); expect(html).not.toContain('<img')
  })
  it('shows generation progress during an explicit retry before the failed job is replaced', () => {
    const failed = { ...job, status: 'failed' as const, failure_code: 'verification_failed' }
    const html = renderToStaticMarkup(<VariantGenerationCard job={failed} busy disabled generating onOpen={() => {}} onRetry={() => {}} />)
    expect(html).toContain('正在围绕原题'); expect(html).toContain('spin')
    expect(html).not.toContain('未通过核验'); expect(html).not.toContain('再生成一次')
    const groupHtml = renderToStaticMarkup(<VariantGroupCard group={group} generation={failed} busy disabled generating onStart={() => {}} />)
    expect(groupHtml).toContain('正在围绕原题'); expect(groupHtml).toContain('准备中，可先做自己的作业')
  })
  it('shows progress for a first generation before a job exists without changing ready-session opening', () => {
    const html = renderToStaticMarkup(<VariantGroupCard group={group} busy disabled generating onStart={() => {}} />)
    expect(html).toContain('正在围绕原题'); expect(html).toContain('准备中，可先做自己的作业')
    const ready = renderToStaticMarkup(<VariantGenerationCard job={{ ...job, status: 'ready' }} busy disabled onOpen={() => {}} onRetry={() => {}} />)
    expect(ready).toContain('题目已通过 AI 核验'); expect(ready).not.toContain('正在围绕原题')
  })
})
