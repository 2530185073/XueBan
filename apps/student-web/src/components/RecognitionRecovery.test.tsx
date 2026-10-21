import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import type { RecognitionRecoveryStatus } from '../core/types'
import { RecognitionRecoveryCard } from './RecognitionRecovery'
const status: RecognitionRecoveryStatus = { case_id: 'case_one', attempt_id: 'attempt_one', limit: 2, used: 2, remaining: 0, eligible: false, blocked_reason: 'budget_exhausted', active_job_id: null, last_job_id: 'job_one', last_outcome: 'unchanged_pending' }
const render = (value: RecognitionRecoveryStatus | null, error: string | null = null) => renderToStaticMarkup(<RecognitionRecoveryCard view={{ status: value, loading: false, submitting: false, error }} needed online busy={false} onStart={() => {}} onRefresh={() => {}} />)
const action = (html: string) => html.match(/<button[^>]*>[\s\S]*?重新核对原图<\/button>/)?.[0]
describe('original-image recovery card', () => {
  it('shows server exhaustion and ways to continue without requiring a new student answer', () => {
    const html = render(status)
    expect(html).toContain('已用 2 / 2 次'); expect(html).toContain('先看其他题'); expect(html).toContain('补拍清楚的照片'); expect(html).toContain('请老师帮忙')
    expect(action(html)).toContain('disabled'); expect(html).not.toContain('正确')
  })
  it('offers the remaining server budget after a service failure, without implying correction passed', () => {
    const html = render({ ...status, used: 1, remaining: 1, eligible: true, blocked_reason: null, last_outcome: 'service_failed' })
    expect(html).toContain('这次核对服务未完成'); expect(html).toContain('还可核对 1 次'); expect(action(html)).not.toContain('disabled'); expect(html).not.toContain('正确')
  })
  it('does not invent a budget when status is unknown and requests a read-only refresh after errors', () => {
    const html = render(null, '网络中断')
    expect(html).not.toContain('还可核对'); expect(action(html)).toContain('disabled'); expect(html).toContain('请先刷新状态确认进度')
  })
})
