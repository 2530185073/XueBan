import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import type { VariantErrorGroup, VariantGeneration } from '../core'
import { variantOverview } from '../core/learning.fixtures'
import { generationCopy, generationPending, VariantGenerationCard, VariantGroupCard } from './VariantPracticePage'

const group = variantOverview.groups[0] as VariantErrorGroup
const job: VariantGeneration = { job_id: 'generation_one', student_id: 'student_one', source_case_id: 'case_one', graph_version: 'g1', knowledge_id: group.knowledge_id, question_type: 'calculation', status: 'queued', item_id: null, failure_code: null, notice: '合成测试' }
const render = (value: VariantErrorGroup, generation?: VariantGeneration) => renderToStaticMarkup(<VariantGroupCard group={value} generation={generation} disabled={false} busy={false} onStart={() => {}} />)
const renderJob = (value: VariantGeneration) => renderToStaticMarkup(<VariantGenerationCard job={value} disabled={false} busy={false} onOpen={() => {}} onRetry={() => {}} />)

describe('voluntary generation availability', () => {
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
})
