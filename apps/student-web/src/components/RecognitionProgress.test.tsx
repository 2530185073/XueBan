import nullableEndpointFixture from '../core/recognitionProgress.fixture.json'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { IDBFactory } from 'fake-indexeddb'
import { RecognitionDraftCard } from './RecognitionProgress'
import { isRecognitionProgress, type RecognitionProgress } from '../core/recognitionProgress'
import { StudentClient } from '../core/student'
import { DraftStore } from '../core/store'

const progress = (): RecognitionProgress => ({ protocol: 'recognition-draft.v1', job_id: 'job_one', homework_id: 'hw_one', student_id: 'student_a', state: 'reading', pages: [{ asset_id: 'asset_one', completed_reads: 1, draft: [{ label: '1', stem_text: '计算 1+1', answer_text: '  学生答案：3\n－1  ' }] }] })
const json = (value: unknown) => new Response(JSON.stringify(value), { headers: { 'Content-Type': 'application/json' } })
class MemorySession implements Storage {
  values = new Map<string, string>()
  get length() { return this.values.size }
  clear() { this.values.clear() }
  getItem(k: string) { return this.values.get(k) ?? null }
  key(i: number) { return [...this.values.keys()][i] ?? null }
  removeItem(k: string) { this.values.delete(k) }
  setItem(k: string, v: string) { this.values.set(k, v) }
}
function setup() {
  const calls: { path: string; method: string }[] = []
  let handler = async () => json(progress())
  const transport: typeof fetch = async (input, init = {}) => {
    const path = new URL(String(input)).pathname, student = ((init.headers as Record<string, string>)?.Authorization ?? '').split('.')[1]
    calls.push({ path, method: init.method ?? 'GET' })
    if (path === '/v1/auth/login') return json({ access_token: 'synthetic.' + JSON.parse(String(init.body)).username.split('.')[1] + '.token', expires_at: '2099-01-01T00:00:00Z', must_change_password: false })
    if (path === '/v1/auth/account') return json({ username: 'synthetic.' + student, student_id: 'student_' + student, must_change_password: false })
    if (path === '/v1/me') return json({ actor_id: 'actor_' + student, roles: ['student'], authorized_students: [{ student_id: 'student_' + student, campus_id: 'campus_one', display_name: 'Synthetic' }] })
    if (path.endsWith('/recognition-progress')) return handler()
    throw new Error('unexpected route')
  }
  const client = new StudentClient({ origin: 'http://localhost', transport, storage: new MemorySession(), store: new DraftStore(new IDBFactory()) })
  return { client, calls, set: (next: typeof handler) => { handler = next }, login: (student = 'a') => client.login('synthetic.' + student, 'synthetic password') }
}

describe('unverified completed read stays a private draft projection', () => {
  it('renders exact transcription and disclaimer without any grade, answer, or subject controls', () => {
    const value = progress(), before = structuredClone(value)
    const html = renderToStaticMarkup(<RecognitionDraftCard value={value} />)
    expect(html).toContain('AI 识读草稿 · 核对仍在进行')
    expect(html).toContain('  学生答案：3\n－1  ')
    expect(html).toContain('暂不判断对错')
    expect(html).toContain('非题目总数')
    expect(html).not.toMatch(/我的作答|正确答案|知识薄弱|<select|<button/)
    expect(value).toEqual(before)
  })
  it('does not render earlier draft once final result is available', () => {
    expect(renderToStaticMarkup(<RecognitionDraftCard value={{ ...progress(), state: 'final_available', pages: [] }} />)).toBe('')
    expect(renderToStaticMarkup(<RecognitionDraftCard value={null} />)).toBe('')
  })
  it.each(['verdict', 'correct_answer', 'subject', 'knowledge_points'])('rejects additional %s in public draft DTO', field => {
    const value = progress(); Object.assign(value.pages[0].draft[0], { [field]: 'forged' })
    expect(isRecognitionProgress(value)).toBe(false)
  })
  it('fresh read after reload has same draft and final read replaces it without any POST', async () => {
    const s = setup(); await s.login()
    const first = await s.client.recognitionProgress('job_one', 'hw_one', ['asset_one'])
    await s.client.restore()
    expect(await s.client.recognitionProgress('job_one', 'hw_one', ['asset_one'])).toEqual(first)
    s.set(async () => json({ ...progress(), state: 'final_available', pages: [] }))
    expect((await s.client.recognitionProgress('job_one', 'hw_one', ['asset_one'])).pages).toEqual([])
    expect(s.calls.filter(c => c.method !== 'GET').map(c => c.path)).toEqual(['/v1/auth/login'])
  })
  it.each(['student_id', 'job_id', 'homework_id', 'asset_id'])('rejects foreign %s even on successful response', async field => {
    const s = setup(); await s.login(); const value = progress()
    if (field === 'asset_id') value.pages[0].asset_id = 'asset_foreign'
    else Object.assign(value, { [field]: 'foreign' })
    s.set(async () => json(value))
    await expect(s.client.recognitionProgress('job_one', 'hw_one', ['asset_one'])).rejects.toMatchObject({ code: 'invalid_response' })
  })
  it('late old progress cannot cross same-token restore session', async () => {
    const s = setup(); await s.login()
    let release!: () => void, entered!: () => void
    const waiting = new Promise<void>(r => { entered = r }), gate = new Promise<void>(r => { release = r })
    s.set(async () => { entered(); await gate; return json(progress()) })
    const old = s.client.recognitionProgress('job_one', 'hw_one', ['asset_one']).catch(e => e.code)
    await waiting; await s.client.restore(); release()
    expect(await old).toBe('session_changed')
    expect(s.client.selectedStudentId).toBe('student_a')
  })
})

it('accepts the backend-checked nullable response without dropping its readable neighbor', () => {
  expect(isRecognitionProgress(nullableEndpointFixture)).toBe(true)
  const html = renderToStaticMarkup(<RecognitionDraftCard value={nullableEndpointFixture as RecognitionProgress} />)
  expect(html).toContain('题干文字暂未读清')
  expect(html).toContain('暂未取得作答文字')
  expect(html).toContain('4')
  expect(html).not.toContain('学生未作答')
})
