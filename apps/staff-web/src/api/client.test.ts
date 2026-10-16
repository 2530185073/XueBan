import { describe, expect, it, vi } from 'vitest'
import { ApiError, errorMessage, normalizeBaseUrl, StaffApi } from './client'
import { sampleHelp, sampleIdentity, sampleWorkspace } from '../sample'
import { matchesContract } from './validation'
import type { SubmitReview } from './contracts.generated'

const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } })
const asFetch = (mock: ReturnType<typeof vi.fn>) => mock as unknown as typeof fetch

describe('一期接口映射', () => {
  it('未提供令牌时不发出请求；地址拒绝外网HTTP和内嵌凭据', () => {
    const transport = vi.fn()
    expect(() => new StaffApi('https://example.test', '', asFetch(transport))).toThrow(ApiError)
    expect(transport).not.toHaveBeenCalled()
    expect(() => normalizeBaseUrl('http://example.test')).toThrow()
    expect(() => normalizeBaseUrl('https://user:password@example.test')).toThrow()
    expect(() => normalizeBaseUrl('https://example.test?token=secret')).toThrow()
    expect(normalizeBaseUrl('http://127.0.0.1:8000/')).toBe('http://127.0.0.1:8000')
  })
  it('读取身份与游标求助页，授权只在请求头中', async () => {
    const transport = vi.fn().mockResolvedValueOnce(response(sampleIdentity)).mockResolvedValueOnce(response({ items: [sampleHelp], next_cursor: null }))
    const client = new StaffApi('https://example.test/', 'session-token', asFetch(transport))
    expect(await client.me()).toEqual(sampleIdentity)
    await client.helpQueue('cursor value')
    expect(transport.mock.calls[0][0]).toBe('https://example.test/v1/me')
    expect(transport.mock.calls[0][1].headers.Authorization).toBe('Bearer session-token')
    expect(transport.mock.calls[0][1].credentials).toBe('omit')
    expect(transport.mock.calls[1][0]).toBe('https://example.test/v1/help-requests?limit=30&cursor=cursor+value')
    client.disconnect()
    await expect(client.me()).rejects.toMatchObject({ status: 401 })
    expect(transport).toHaveBeenCalledTimes(2)
  })
  it('接手只提交求助版本与幂等键，不伪造处理人', async () => {
    const claimed = { ...sampleHelp, revision: 2, status: 'claimed', claimed_by: 'staff_preview', claimed_at: '2026-10-16T08:35:00Z' }
    const transport = vi.fn().mockResolvedValue(response(claimed))
    const client = new StaffApi('https://example.test', 'token', asFetch(transport))
    await client.claim(sampleHelp)
    const [url, init] = transport.mock.calls[0]
    expect(url).toBe('https://example.test/v1/help-requests/help_synthetic/claim')
    expect(JSON.parse(init.body)).toEqual({ expected_revision: 1 })
    expect(init.headers['Idempotency-Key']).toMatch(/^[a-z0-9-]{36}$/)
  })
  it('详情按现有case/question/attempt/decision/hint/asset接口组合读取', async () => {
    const values = [sampleWorkspace.correction, sampleWorkspace.question, sampleWorkspace.attempts[0], sampleWorkspace.decision, sampleWorkspace.hints[0], sampleWorkspace.assets[0]]
    const transport = vi.fn()
    values.forEach(value => transport.mockResolvedValueOnce(response(value)))
    const client = new StaffApi('https://example.test', 'token', asFetch(transport))
    await client.case('case_synthetic'); await client.question('question_synthetic'); await client.attempt('attempt_synthetic')
    await client.decision('decision_synthetic'); await client.hint('hint_synthetic'); await client.asset('asset_synthetic')
    expect(transport.mock.calls.map(call => call[0])).toEqual([
      'https://example.test/v1/cases/case_synthetic', 'https://example.test/v1/questions/question_synthetic',
      'https://example.test/v1/attempts/attempt_synthetic', 'https://example.test/v1/decisions/decision_synthetic',
      'https://example.test/v1/hints/hint_synthetic', 'https://example.test/v1/assets/asset_synthetic/access',
    ])
  })
  it('复核提交有效版本与人工依据；请求不接受任意身份字段', async () => {
    const payload: SubmitReview = { expected_revision: 3, target_decision_id: 'decision_synthetic', target_decision_revision: 1, action: 'confirm', verdict: 'incorrect', reason: '对照了原图', basis: [{ kind: 'staff_observation', summary: '第二项未分配系数', source_ref: null, regions: [] }], attempt_id: 'attempt_synthetic', question_revision: 1, attempt_recognition_revision: 1 }
    const review = { id: 'review_synthetic', case_id: 'case_synthetic', target_decision_id: 'decision_synthetic', target_decision_revision: 1, action: 'confirm', new_decision_id: 'decision_reviewed', reason: '对照了原图', reviewer_id: 'staff_preview', created_at: '2026-10-16T08:35:00Z' }
    const transport = vi.fn().mockResolvedValue(response(review, 201))
    const client = new StaffApi('https://example.test', 'token', asFetch(transport))
    await client.review('case_synthetic', payload)
    expect(transport.mock.calls[0][0]).toBe('https://example.test/v1/cases/case_synthetic/reviews')
    expect(JSON.parse(transport.mock.calls[0][1].body)).toEqual(payload)
    await expect(client.review('case_synthetic', { ...payload, reviewer_id: 'intruder' } as SubmitReview)).rejects.toMatchObject({ status: 422 })
    expect(transport).toHaveBeenCalledTimes(1)
  })
})

describe('失败、冲突与未知提交结果', () => {
  it.each([401, 403, 409, 503])('HTTP %s 不伪装成功，返回可识别错误', async status => {
    const transport = vi.fn().mockResolvedValue(response({ status }, status))
    const client = new StaffApi('https://example.test', 'token', asFetch(transport))
    let caught: unknown
    try { await client.helpQueue() } catch (error) { caught = error }
    expect(caught).toBeInstanceOf(ApiError)
    expect((caught as ApiError).status).toBe(status)
    expect(errorMessage(caught).length).toBeGreaterThan(10)
  })
  it('超时后保留同键同内容；变更提交受阻，原操作可安全重试', async () => {
    const claimed = { ...sampleHelp, revision: 2, status: 'claimed', claimed_by: 'staff_preview', claimed_at: '2026-10-16T08:35:00Z' }
    const transport = vi.fn().mockRejectedValueOnce(new TypeError('network')).mockResolvedValueOnce(response(claimed))
    const client = new StaffApi('https://example.test', 'token', asFetch(transport))
    await expect(client.claim(sampleHelp)).rejects.toMatchObject({ status: 0 })
    expect(client.hasPendingWrite()).toBe(true)
    await expect(client.claim({ ...sampleHelp, revision: 2 })).rejects.toMatchObject({ status: 409 })
    expect(transport).toHaveBeenCalledTimes(1)
    await client.retryPendingWrite()
    expect(transport.mock.calls[1][1].headers['Idempotency-Key']).toBe(transport.mock.calls[0][1].headers['Idempotency-Key'])
    expect(transport.mock.calls[1][1].body).toBe(transport.mock.calls[0][1].body)
    expect(client.hasPendingWrite()).toBe(false)
  })
  it('不完整成功响应也不能显示队列或确认完成', async () => {
    const transport = vi.fn().mockResolvedValue(response({ items: [] }))
    const client = new StaffApi('https://example.test', 'token', asFetch(transport))
    await expect(client.helpQueue()).rejects.toMatchObject({ status: 502 })
  })
  it('合成预览的各对象符合共享契约，且无真实个人信息', () => {
    expect(matchesContract('data', 'HelpRequest', sampleHelp)).toBe(true)
    expect(matchesContract('data', 'IdentityContext', sampleIdentity)).toBe(true)
    expect(matchesContract('data', 'CorrectionCase', sampleWorkspace.correction)).toBe(true)
    expect(matchesContract('data', 'QuestionRead', sampleWorkspace.question)).toBe(true)
    expect(matchesContract('data', 'DecisionRead', sampleWorkspace.decision)).toBe(true)
    for (const attempt of sampleWorkspace.attempts) expect(matchesContract('data', 'AttemptRead', attempt)).toBe(true)
    for (const hint of sampleWorkspace.hints) expect(matchesContract('data', 'Hint', hint)).toBe(true)
    for (const asset of sampleWorkspace.assets) expect(matchesContract('data', 'AssetAccess', asset)).toBe(true)
  })
})
