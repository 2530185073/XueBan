import { describe, expect, it, vi } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { AccountOperations, normalizeCreate, validPassword } from './operations'
import { account, listAccounts } from './api'
import { StudentAccounts } from './StudentAccounts'
import type { AdminClient, CreateAccount, StudentAccount } from './types'

// Synthetic values exist only in tests; no production default password exists.
const secret = '  synthetic password  '
const input: CreateAccount = { username: '  STUDENT.ONE ', student_name: ' 合成学员 ', campus_id: 'campus_synthetic', initial_password: secret }
const item: StudentAccount = { student_id: 'student_synthetic', student_name: '合成学员', username: 'student.one', campus_id: input.campus_id, principal_id: 'actor_synthetic', active: true, must_change_password: true }
function client(handler: (path: string, options?: Parameters<AdminClient['adminRequest']>[1]) => Promise<unknown>) {
  const request = vi.fn(handler)
  return { value: { adminRequest: request } as AdminClient, request }
}

describe('account management keeps credentials and retry identity in page memory', () => {
  it('normalizes account identifiers without changing password bytes or whitespace', () => {
    expect(normalizeCreate(input)).toEqual({ ...input, username: 'student.one', student_name: '合成学员' })
    expect(normalizeCreate(input).initial_password).toBe(secret)
    expect(validPassword('a'.repeat(14))).toBe(false)
    expect(validPassword('字'.repeat(15))).toBe(true)
    expect(validPassword('😀'.repeat(128))).toBe(true)
    expect(validPassword('a'.repeat(129))).toBe(false)
    expect(() => normalizeCreate({ ...input, username: '错误用户名' })).toThrow()
  })
  it('retries an unknown creation with the same immutable body and key, never replacing it with another student', async () => {
    const api = client(async () => { if (api.request.mock.calls.length === 1) throw new Error('网络结果未知'); return item })
    const operations = new AccountOperations(api.value, () => 'synthetic-operation-key')
    await expect(operations.create(input)).rejects.toThrow('网络结果未知')
    await expect(operations.create({ ...input, username: 'different.student' })).rejects.toThrow('上一项操作')
    expect(api.request).toHaveBeenCalledTimes(1)
    expect(operations.pendingSummary).toEqual({ kind: 'create', username: 'student.one', studentName: '合成学员' })
    expect(JSON.stringify(operations.pendingSummary)).not.toContain(secret)
    const result = await operations.retry()
    expect(api.request.mock.calls[0]).toEqual(api.request.mock.calls[1])
    expect(result.password).toBe(secret); expect(operations.pendingSummary).toBeNull()
  })
  it('keeps an unknown request through authorization denial and later retries the original key', async () => {
    const api = client(async () => { if (api.request.mock.calls.length < 3) throw Object.assign(new Error('暂时无权访问'), { status: 403 }); return item })
    const operations = new AccountOperations(api.value)
    await expect(operations.create(input)).rejects.toThrow()
    await expect(operations.retry()).rejects.toThrow()
    await operations.retry()
    expect(api.request.mock.calls[0]).toEqual(api.request.mock.calls[2])
  })
  it('uses the authoritative list only to find a username, not to infer the requested password succeeded', async () => {
    const api = client(async (_path, options) => { if (options?.method === 'POST') throw new Error('响应未收到'); return { items: [item], campuses: [{ campus_id: item.campus_id }] } })
    const operations = new AccountOperations(api.value)
    await expect(operations.create(input)).rejects.toThrow()
    expect(operations.observedCreation(await operations.list())?.username).toBe(item.username)
    expect(operations.pendingSummary?.kind).toBe('create')
    expect(api.request.mock.calls.filter(call => call[1]?.method === 'POST')).toHaveLength(1)
  })
  it('never turns an uncertain reset into success from a must-change-password list flag', async () => {
    let fail = true
    const api = client(async (_path, options) => {
      if (!options) return { items: [item], campuses: [{ campus_id: item.campus_id }] }
      if (fail) { fail = false; throw new Error('重置结果未知') }
      return { student_id: item.student_id, must_change_password: true }
    })
    const operations = new AccountOperations(api.value)
    await expect(operations.reset(item, secret)).rejects.toThrow()
    await operations.list(); expect(operations.pendingSummary?.kind).toBe('reset')
    const receipt = await operations.retry()
    expect(receipt.kind).toBe('reset'); expect(receipt.password).toBe(secret)
    expect(api.request.mock.calls[0]).toEqual(api.request.mock.calls[2])
  })
  it('rejects malformed or mismatched server confirmation while preserving the original retry', async () => {
    const api = client(async () => ({ ...item, username: 'another.student' }))
    const operations = new AccountOperations(api.value)
    await expect(operations.create(input)).rejects.toThrow('不一致')
    expect(operations.pendingSummary).not.toBeNull()
    operations.discard(); expect(operations.pendingSummary).toBeNull()
  })
  it('rejects a reset confirmation for a different student', async () => {
    const api = client(async () => ({ student_id: 'student_someone_else', must_change_password: true }))
    const operations = new AccountOperations(api.value)
    await expect(operations.reset(item, secret)).rejects.toThrow('尚未得到确认')
    expect(operations.pendingSummary?.kind).toBe('reset')
  })
  it('clears memory state on unmount and never reveals a late response receipt', async () => {
    let finish!: (value: unknown) => void
    const api = client(() => new Promise(resolve => { finish = resolve }))
    const operations = new AccountOperations(api.value)
    const pending = operations.create(input)
    operations.dispose(); finish(item)
    await expect(pending).rejects.toThrow('页面已关闭')
    expect(operations.pendingSummary).toBeNull()
    await expect(operations.retry()).rejects.toThrow('没有可重试')
  })
  it('does not invoke the API for invalid passwords or a second in-flight submission', async () => {
    const api = client(() => new Promise(() => undefined))
    const operations = new AccountOperations(api.value)
    await expect(operations.create({ ...input, initial_password: 'too short' })).rejects.toThrow('15–128')
    expect(api.request).not.toHaveBeenCalled()
    void operations.create(input)
    await expect(operations.retry()).rejects.toThrow('正在处理')
    expect(api.request).toHaveBeenCalledTimes(1)
    operations.dispose()
  })
})

it('restricts the roster to declared fields and rejects ambiguous duplicate accounts', async () => {
  expect(account({ ...item, password: secret })).not.toHaveProperty('password')
  const api = client(async () => ({ items: [item, item], campuses: [{ campus_id: item.campus_id }] }))
  await expect(listAccounts(api.value)).rejects.toThrow('重复记录')
})

it('renders an empty real account form without a default password, hidden technical identities or mock roster', () => {
  const api = client(async () => { throw new Error('Effects do not run during this render') })
  const markup = renderToStaticMarkup(<StudentAccounts client={api.value} />)
  expect(markup).toContain('添加一位学员')
  expect(markup).toContain('初始临时密码')
  expect(markup).not.toContain(secret)
  expect(markup).not.toContain(item.student_id)
  expect(markup).not.toContain('localStorage')
  const passwordInput = markup.match(/<input[^>]*id="admin-initial-password"[^>]*>/)?.[0]
  expect(passwordInput).toBeDefined()
  expect(passwordInput).not.toMatch(/minLength|maxLength/i)
  expect(api.request).not.toHaveBeenCalled()
})
