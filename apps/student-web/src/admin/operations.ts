import { account, accountsPath, AdminError, listAccounts } from './api'
import type { AccountList, AdminClient, CreateAccount, CredentialReceipt, PendingSummary, StudentAccount } from './types'

type Pending = { kind: 'create'; key: string; input: CreateAccount } | { kind: 'reset'; key: string; student: StudentAccount; input: { new_password: string } }
export function validPassword(password: string): boolean {
  const length = Array.from(password).length
  return length >= 15 && length <= 128 && new TextEncoder().encode(password).length <= 512
}
export function normalizeCreate(input: CreateAccount): CreateAccount {
  const normalized = { ...input, username: input.username.trim().toLowerCase(), student_name: input.student_name.trim() }
  if (!/^[a-z0-9][a-z0-9._-]{2,63}$/.test(normalized.username)) throw new AdminError('用户名需要 3–64 位英文字母、数字或 . _ -，并以字母或数字开头。')
  if (!normalized.student_name || Array.from(normalized.student_name).length > 80) throw new AdminError('请填写学员姓名，最多 80 个字。')
  if (!normalized.campus_id) throw new AdminError('请选择已授权校区。')
  if (!validPassword(input.initial_password)) throw new AdminError('密码需要 15–128 个字符；空格会作为密码的一部分保留。')
  return normalized
}

/** Secrets and idempotency keys live only in this mounted page's memory. */
export class AccountOperations {
  private pending: Pending | null = null
  private working = false
  private disposed = false
  constructor(private client: AdminClient, private key: () => string = () => crypto.randomUUID()) {}
  get pendingSummary(): PendingSummary | null {
    const p = this.pending
    return !p ? null : p.kind === 'create' ? { kind: p.kind, studentName: p.input.student_name, username: p.input.username } : { kind: p.kind, studentName: p.student.student_name, username: p.student.username }
  }
  dispose() { this.disposed = true; this.pending = null }
  discard() { if (this.working) throw new AdminError('请等待当前请求结束。'); this.pending = null }
  async list() { if (this.disposed) throw new AdminError('页面已关闭。'); return listAccounts(this.client) }
  observedCreation(list: AccountList): StudentAccount | null { const pending = this.pending; return pending?.kind === 'create' ? list.items.find(row => row.username === pending.input.username) ?? null : null }
  async create(input: CreateAccount): Promise<CredentialReceipt> {
    if (this.disposed) throw new AdminError('页面已关闭。')
    if (this.pending) throw new AdminError('上一项操作的结果尚未确认，请先重试或核对。')
    this.pending = { kind: 'create', key: this.key(), input: normalizeCreate(input) }
    return this.retry()
  }
  async reset(student: StudentAccount, password: string): Promise<CredentialReceipt> {
    if (this.disposed) throw new AdminError('页面已关闭。')
    if (this.pending) throw new AdminError('上一项操作的结果尚未确认，请先重试或核对。')
    if (!validPassword(password)) throw new AdminError('密码需要 15–128 个字符；空格会作为密码的一部分保留。')
    this.pending = { kind: 'reset', key: this.key(), student: { ...student }, input: { new_password: password } }
    return this.retry()
  }
  async retry(): Promise<CredentialReceipt> {
    if (this.disposed || !this.pending) throw new AdminError('当前没有可重试的操作。')
    if (this.working) throw new AdminError('当前操作正在处理，请稍等。')
    this.working = true
    const pending = this.pending
    try {
      const endpoint = pending.kind === 'create' ? accountsPath : `${accountsPath}/${encodeURIComponent(pending.student.student_id)}/reset-password`
      const value = await this.client.adminRequest<unknown>(endpoint, { method: 'POST', body: { ...pending.input }, idempotencyKey: pending.key })
      if (this.disposed) throw new AdminError('页面已关闭，请重新登录后核对账号。')
      let receipt: CredentialReceipt
      if (pending.kind === 'create') {
        const created = account(value)
        if (created.username !== pending.input.username || created.campus_id !== pending.input.campus_id || created.student_name !== pending.input.student_name || !created.must_change_password) throw new AdminError('创建结果与提交信息不一致，请核对账号列表。')
        receipt = { kind: 'create', studentName: created.student_name, username: created.username, password: pending.input.initial_password }
      } else {
        const reset = value as { student_id?: unknown; must_change_password?: unknown } | null
        if (!reset || reset.student_id !== pending.student.student_id || reset.must_change_password !== true) throw new AdminError('重置结果尚未得到确认，请重试原操作。')
        receipt = { kind: 'reset', studentName: pending.student.student_name, username: pending.student.username, password: pending.input.new_password }
      }
      this.pending = null
      return receipt
    } finally { this.working = false }
  }
}
