import { describe, expect, it, vi } from 'vitest'
import { ApiError, errorMessage, isConflict, StaffApi, UNCONFIRMED_WRITE } from './client'
import { sampleHelp, sampleIdentity, sampleQuestions, sampleReviewItem, sampleWorkspace } from '../sample'
import { matchesContract } from './validation'
import { isReviewQueuePage } from './types'
import type { RepairLayout, SubmitReview } from './contracts.generated'

const ORIGIN = 'http://127.0.0.1:8000'
const response = (value: unknown, status = 200, type = 'application/json') => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': type } })
const session = (expires = '2099-01-01T00:00:00Z', mustChange = false) => ({ access_token: 'session-token', expires_at: expires, must_change_password: mustChange })
const problem = (status: number, code: string, current_revision: number | null = null) => response({ type: `https://errors.xueban.invalid/${code}`, title: code, status, category: 'conflict', code, detail: '合成错误', request_id: 'request_test', current_revision, retry_after_seconds: null }, status)

async function signedIn(...responses: (Response | Error)[]) {
  const transport = vi.fn().mockResolvedValueOnce(response(session()))
  for (const item of responses) item instanceof Error ? transport.mockRejectedValueOnce(item) : transport.mockResolvedValueOnce(item)
  const client = new StaffApi(ORIGIN, { transport: transport as unknown as typeof fetch })
  await client.login('Teacher.A', 'synthetic-password-123')
  return { client, transport, call: (index: number) => ({ url: transport.mock.calls[index][0] as string, init: transport.mock.calls[index][1] as RequestInit & { headers: Record<string, string> } }) }
}

describe('账号登录与会话', () => {
  it('主动复核读取全部题，默认待核接口继续独立', async () => {
    const candidate = { ...sampleReviewItem, reason: 'ai_initial' }
    const unfinished = { ...sampleReviewItem, case_id: 'case_' + 'b'.repeat(32), reason: 'ai_pending' }
    const page = { items: [candidate, unfinished], next_cursor: null }
    const { client, call } = await signedIn(response({ items: [], next_cursor: null }), response(page))
    expect((await client.reviewQueue()).items).toEqual([])
    expect(new URL(call(1).url).searchParams.has('view')).toBe(false)
    expect(await client.reviewQueue(undefined, undefined, 'all')).toEqual(page)
    expect(new URL(call(2).url).searchParams.get('view')).toBe('all')
    expect(call(2).init.headers.Authorization).toBe('Bearer session-token')
  })

  it('同源匿名登录，令牌只出现在后续请求头中', async () => {
    const { client, call } = await signedIn(response(sampleIdentity))
    const login = call(0)
    expect(login.url).toBe(`${ORIGIN}/v1/auth/login`)
    expect(login.init.headers.Authorization).toBeUndefined()
    expect(login.init.headers['Idempotency-Key']).toMatch(/^[a-f0-9-]{36}$/)
    expect(JSON.parse(login.init.body as string)).toEqual({ username: 'teacher.a', password: 'synthetic-password-123' })
    expect(await client.me()).toEqual(sampleIdentity)
    expect(call(1).init.headers.Authorization).toBe('Bearer session-token')
    expect(call(1).init.credentials).toBe('omit')
    expect(call(1).url).not.toContain('session-token')
  })
  it('只接受干净的同源地址', () => {
    expect(() => new StaffApi('https://example.test/staff/')).toThrow()
    expect(() => new StaffApi('https://user:pw@example.test')).toThrow()
    expect(() => new StaffApi('ftp://example.test')).toThrow()
    expect(new StaffApi('http://127.0.0.1:8000/').origin).toBe(ORIGIN)
  })
  it('登录结果未知时重试沿用同一幂等键', async () => {
    const transport = vi.fn().mockRejectedValueOnce(new TypeError('network')).mockResolvedValueOnce(response(session()))
    const client = new StaffApi(ORIGIN, { transport: transport as unknown as typeof fetch })
    await expect(client.login('teacher.a', 'pw-synthetic-0001')).rejects.toMatchObject({ status: 0 })
    await client.login('teacher.a', 'pw-synthetic-0001')
    expect(transport.mock.calls[1][1].headers['Idempotency-Key']).toBe(transport.mock.calls[0][1].headers['Idempotency-Key'])
    expect(client.authenticated).toBe(true)
  })
  it('首次登录要求改密码；改密码返回新会话', async () => {
    const transport = vi.fn().mockResolvedValueOnce(response(session('2099-01-01T00:00:00Z', true))).mockResolvedValueOnce(response({ ...session(), access_token: 'second-token' }))
    const client = new StaffApi(ORIGIN, { transport: transport as unknown as typeof fetch })
    expect((await client.login('teacher.a', 'initial-password-1')).must_change_password).toBe(true)
    await client.changePassword('initial-password-1', 'a brand new synthetic phrase')
    expect(transport.mock.calls[1][0]).toBe(`${ORIGIN}/v1/auth/change-password`)
    expect(transport.mock.calls[1][1].headers.Authorization).toBe('Bearer session-token')
    expect(JSON.parse(transport.mock.calls[1][1].body)).toEqual({ current_password: 'initial-password-1', new_password: 'a brand new synthetic phrase' })
  })
  it('改密码时输错当前密码不清除会话', async () => {
    const { client } = await signedIn(response({ code: 'invalid_credentials', status: 401 }, 401))
    await expect(client.changePassword('wrong-password', 'a brand new synthetic phrase')).rejects.toMatchObject({ status: 401, code: 'invalid_credentials' })
    expect(client.authenticated).toBe(true)
  })
  it('401 清除会话并通知界面；之后不再发请求', async () => {
    const { client, transport } = await signedIn(problem(401, 'authentication_required'))
    const ended = vi.fn(); client.onSessionEnded = ended
    await expect(client.helpQueue()).rejects.toMatchObject({ status: 401 })
    expect(ended).toHaveBeenCalledWith('revoked')
    expect(client.authenticated).toBe(false)
    await expect(client.me()).rejects.toMatchObject({ status: 401 })
    expect(transport).toHaveBeenCalledTimes(2)
  })
  it('会话到期后本地拒绝请求并通知到期', async () => {
    let now = Date.parse('2026-10-16T08:00:00Z')
    const transport = vi.fn().mockResolvedValueOnce(response(session('2026-10-16T09:00:00Z')))
    const client = new StaffApi(ORIGIN, { transport: transport as unknown as typeof fetch, now: () => now })
    await client.login('teacher.a', 'synthetic-password-123')
    const ended = vi.fn(); client.onSessionEnded = ended
    now = Date.parse('2026-10-16T09:00:01Z')
    await expect(client.me()).rejects.toMatchObject({ status: 401, code: 'session_expired' })
    expect(ended).toHaveBeenCalledWith('expired')
    expect(transport).toHaveBeenCalledTimes(1)
  })
  it('退出登录即使服务端未确认也清除本地会话', async () => {
    const { client, call } = await signedIn(new TypeError('offline'))
    expect(await client.logout()).toBe(false)
    expect(call(1).url).toBe(`${ORIGIN}/v1/auth/logout`)
    expect(JSON.parse(call(1).init.body as string)).toEqual({})
    expect(client.authenticated).toBe(false)
  })
})

describe('队列与案例读取', () => {
  it('求助与待核实两个队列分别带游标读取', async () => {
    const { client, call } = await signedIn(response({ items: [sampleHelp], next_cursor: null }), response({ items: [sampleReviewItem], next_cursor: null }))
    await client.helpQueue('help_abc')
    await client.reviewQueue('case_' + 'a'.repeat(32))
    expect(call(1).url).toBe(`${ORIGIN}/v1/help-requests?limit=30&cursor=help_abc`)
    expect(call(2).url).toBe(`${ORIGIN}/v1/review-queue?limit=30&cursor=case_${'a'.repeat(32)}`)
  })
  it('待核实队列条目形状不符时不显示', async () => {
    const { client } = await signedIn(response({ items: [{ ...sampleReviewItem, reason: 'guess' }], next_cursor: null }))
    await expect(client.reviewQueue()).rejects.toMatchObject({ status: 502 })
    expect(isReviewQueuePage({ items: [sampleReviewItem], next_cursor: null })).toBe(true)
  })
  it('按版本读取题目，并读取作业、图片、任务', async () => {
    const { client, call } = await signedIn(response(sampleWorkspace.question), response(sampleWorkspace.homework), response(sampleWorkspace.assets[0]))
    await client.question('question_synthetic', 2); await client.homework('homework_synthetic'); await client.imageAsset('asset_synthetic')
    expect([call(1).url, call(2).url, call(3).url]).toEqual([`${ORIGIN}/v1/questions/question_synthetic?revision=2`, `${ORIGIN}/v1/homeworks/homework_synthetic`, `${ORIGIN}/v1/assets/asset_synthetic`])
  })
})

describe('原图地址', () => {
  const client = new StaffApi(ORIGIN)
  it('接受同源 http 读取地址，拒绝外部来源与非图片路径', () => {
    expect(client.assetUrl(`${ORIGIN}/v1/assets/asset_x1/content?ticket=abc`)).toBe(`${ORIGIN}/v1/assets/asset_x1/content?ticket=abc`)
    expect(client.assetUrl('/v1/assets/asset_x1/content?ticket=abc')).toBe(`${ORIGIN}/v1/assets/asset_x1/content?ticket=abc`)
    for (const bad of ['https://cdn.example.test/v1/assets/asset_x1/content?ticket=abc', `${ORIGIN}/v1/me`, `${ORIGIN}/v1/assets/asset_x1/content#x`, 'http://localhost:8000/v1/assets/asset_x1/content?ticket=a']) {
      expect(() => client.assetUrl(bad)).toThrow(ApiError)
    }
  })
  it('仅开发代理下把本机地址改走同源代理', () => {
    const dev = new StaffApi('http://127.0.0.1:5173', { proxyLoopbackAssets: true })
    expect(dev.assetUrl('http://127.0.0.1:8000/v1/assets/asset_x1/content?ticket=abc')).toBe('http://127.0.0.1:5173/v1/assets/asset_x1/content?ticket=abc')
    expect(() => dev.assetUrl('https://example.test/v1/assets/asset_x1/content?ticket=abc')).toThrow(ApiError)
  })
  it('只接受 JPEG/PNG/WebP 图片', async () => {
    const transport = vi.fn().mockResolvedValueOnce(new Response('x', { headers: { 'Content-Type': 'text/html' } })).mockResolvedValueOnce(new Response(new Uint8Array([1, 2, 3]), { headers: { 'Content-Type': 'image/png' } }))
    const images = new StaffApi(ORIGIN, { transport: transport as unknown as typeof fetch })
    await expect(images.image(`${ORIGIN}/v1/assets/asset_x1/content?ticket=abc`)).rejects.toMatchObject({ code: 'invalid_image' })
    expect((await images.image(`${ORIGIN}/v1/assets/asset_x1/content?ticket=abc`)).type).toBe('image/png')
  })
})

describe('处理操作、冲突与未知结果', () => {
  const review: SubmitReview = { expected_revision: 3, target_decision_id: 'decision_synthetic', target_decision_revision: 1, action: 'confirm', verdict: 'incorrect', reason: '对照了原图', basis: [{ kind: 'staff_observation', summary: '第二项未分配系数', source_ref: null, regions: [] }], attempt_id: 'attempt_synthetic', question_revision: 1, attempt_recognition_revision: 1 }
  const reviewResult = { id: 'review_synthetic', case_id: 'case_synthetic', target_decision_id: 'decision_synthetic', target_decision_revision: 1, action: 'confirm', new_decision_id: 'decision_reviewed', reason: '对照了原图', reviewer_id: 'staff_preview', created_at: '2026-10-16T08:35:00Z' }
  const region = sampleQuestions[1].record.regions[0]
  const repair: RepairLayout = { expected_revision: 4, supersede_questions: [{ question_id: 'question_synthetic', expected_revision: 1 }], replacement_regions: [{ regions: [region], stem_text: null }, { regions: [region], stem_text: '化简' }], reason: '错切' }
  const accepted = { job: { id: 'job_repair', revision: 1, kind: 'repair_layout', status: 'queued', created_at: '2026-10-16T08:35:00Z', updated_at: '2026-10-16T08:35:00Z', retry_after_seconds: 1, failure: null, result: null }, created_resource: { resource_type: 'homework', resource_id: 'homework_synthetic' } }

  it('接手只提交求助版本与幂等键', async () => {
    const { client, call } = await signedIn(response({ ...sampleHelp, revision: 2, status: 'claimed', claimed_by: 'staff_preview', claimed_at: '2026-10-16T08:35:00Z' }))
    await client.claim(sampleHelp)
    expect(call(1).url).toBe(`${ORIGIN}/v1/help-requests/help_synthetic/claim`)
    expect(JSON.parse(call(1).init.body as string)).toEqual({ expected_revision: 1 })
    expect(call(1).init.headers['Idempotency-Key']).toMatch(/^[a-f0-9-]{36}$/)
  })
  it('复核按契约提交；夹带身份字段时本地拒绝', async () => {
    const { client, call, transport } = await signedIn(response(reviewResult, 201))
    await client.submitReview('case_synthetic', review)
    expect(call(1).url).toBe(`${ORIGIN}/v1/cases/case_synthetic/reviews`)
    expect(JSON.parse(call(1).init.body as string)).toEqual(review)
    await expect(client.submitReview('case_synthetic', { ...review, reviewer_id: 'intruder' } as SubmitReview)).rejects.toMatchObject({ status: 422 })
    expect(transport).toHaveBeenCalledTimes(2)
  })
  it('题目、答案修订与补题拆合走各自接口', async () => {
    const question = { ...sampleWorkspace.question.record, revision: 2, source: 'staff_revision', source_actor_id: 'staff_preview' }
    const attempt = { ...sampleWorkspace.attempts[0].record, revision: 2, recognition_revision: 2 }
    const { client, call, transport } = await signedIn(response(question, 201), response(attempt, 201), response(accepted, 202))
    await client.reviseQuestion('question_synthetic', { expected_revision: 1, stem_text: '去括号', regions: [region], reason: '漏字' })
    await client.reviseAnswer('attempt_synthetic', { expected_revision: 1, answer_text: null, answer_regions: [], reason: '看不清' })
    expect((await client.repairLayout('homework_synthetic', repair)).job.kind).toBe('repair_layout')
    expect([call(1).url, call(2).url, call(3).url]).toEqual([`${ORIGIN}/v1/questions/question_synthetic/revisions`, `${ORIGIN}/v1/attempts/attempt_synthetic/recognition-revisions`, `${ORIGIN}/v1/homeworks/homework_synthetic/layout-revisions`])
    expect(JSON.parse(call(3).init.body as string)).toEqual(repair)
    await expect(client.repairLayout('homework_synthetic', { ...repair, replacement_regions: [] })).rejects.toMatchObject({ status: 422 })
    await expect(client.repairLayout('homework_synthetic', { ...repair, replacement_regions: [{ regions: [{ ...region, coordinate_space: 'display' as 'original_pixels' }], stem_text: null }] })).rejects.toMatchObject({ status: 422 })
    expect(transport).toHaveBeenCalledTimes(4)
  })
  it('409 版本冲突可识别，带回最新版本且不锁定后续操作', async () => {
    const { client, transport } = await signedIn(problem(409, 'revision_conflict', 5), response(reviewResult, 201))
    let caught: unknown
    try { await client.submitReview('case_synthetic', review) } catch (error) { caught = error }
    expect(isConflict(caught)).toBe(true)
    expect((caught as ApiError).currentRevision).toBe(5)
    expect(errorMessage(caught)).toContain('已重新读取最新内容')
    expect(client.hasPendingWrite()).toBe(false)
    await client.submitReview('case_synthetic', { ...review, expected_revision: 5 })
    expect(transport).toHaveBeenCalledTimes(3)
  })
  it('结果未知时锁定其他写入，原操作同键同内容重试', async () => {
    const claimed = { ...sampleHelp, revision: 2, status: 'claimed', claimed_by: 'staff_preview', claimed_at: '2026-10-16T08:35:00Z' }
    const { client, call, transport } = await signedIn(new TypeError('network'), response(claimed))
    await expect(client.claim(sampleHelp)).rejects.toMatchObject({ status: 0 })
    expect(client.hasPendingWrite()).toBe(true)
    let blocked: unknown
    try { await client.submitReview('case_synthetic', review) } catch (error) { blocked = error }
    expect(blocked).toMatchObject({ status: 409, code: UNCONFIRMED_WRITE })
    expect(isConflict(blocked)).toBe(false)
    await expect(client.claim({ ...sampleHelp, revision: 2 })).rejects.toMatchObject({ code: UNCONFIRMED_WRITE })
    expect(transport).toHaveBeenCalledTimes(2)
    const { path } = await client.retryPendingWrite()
    expect(path).toBe('/v1/help-requests/help_synthetic/claim')
    expect(call(2).init.headers['Idempotency-Key']).toBe(call(1).init.headers['Idempotency-Key'])
    expect(call(2).init.body).toBe(call(1).init.body)
    expect(client.hasPendingWrite()).toBe(false)
  })
  it('503 保留待确认；确定拒绝(422)则释放', async () => {
    const { client } = await signedIn(problem(503, 'service_unavailable'), problem(422, 'invalid_request'))
    await expect(client.claim(sampleHelp)).rejects.toMatchObject({ status: 503 })
    expect(client.hasPendingWrite()).toBe(true)
    await expect(client.retryPendingWrite()).rejects.toMatchObject({ status: 422 })
    expect(client.hasPendingWrite()).toBe(false)
  })
  it.each([401, 403, 404, 429, 503])('HTTP %s 给出中文说明且不伪装成功', async status => {
    const { client } = await signedIn(response({ status }, status))
    let caught: unknown
    try { await client.helpQueue() } catch (error) { caught = error }
    expect(caught).toBeInstanceOf(ApiError)
    expect((caught as ApiError).status).toBe(status)
    expect(errorMessage(caught)).toMatch(/[\u4e00-\u9fa5]{6,}/)
  })
  it('不完整的成功响应不能显示', async () => {
    const { client } = await signedIn(response({ items: [] }))
    await expect(client.helpQueue()).rejects.toMatchObject({ status: 502 })
  })
})

describe('合成预览', () => {
  it('各对象符合共享契约', () => {
    expect(matchesContract('data', 'HelpRequest', sampleHelp)).toBe(true)
    expect(matchesContract('data', 'IdentityContext', sampleIdentity)).toBe(true)
    expect(matchesContract('data', 'CorrectionCase', sampleWorkspace.correction)).toBe(true)
    expect(matchesContract('data', 'HomeworkSession', sampleWorkspace.homework)).toBe(true)
    expect(matchesContract('data', 'DecisionRead', sampleWorkspace.decision)).toBe(true)
    expect(matchesContract('data', 'CheckRun', sampleWorkspace.check)).toBe(true)
    for (const question of sampleQuestions) expect(matchesContract('data', 'QuestionRead', question)).toBe(true)
    for (const attempt of sampleWorkspace.attempts) expect(matchesContract('data', 'AttemptRead', attempt)).toBe(true)
    for (const hint of sampleWorkspace.hints) expect(matchesContract('data', 'Hint', hint)).toBe(true)
    for (const asset of sampleWorkspace.assets) expect(matchesContract('data', 'ImageAsset', asset)).toBe(true)
    expect(isReviewQueuePage({ items: [sampleReviewItem], next_cursor: null })).toBe(true)
  })
})
