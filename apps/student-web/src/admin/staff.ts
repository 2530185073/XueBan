import { AdminError } from './api'
import { validPassword } from './operations'
import type { AdminClient } from './types'

export const staffPath = '/v1/admin/staff-accounts'
export const STAFF_ROLES = { sitting_teacher: '坐班老师', data_steward: '数据治理' } as const
export type StaffRole = keyof typeof STAFF_ROLES
export interface StaffAccount { principal_id: string; username: string; roles: string[]; campus_ids: string[]; active: boolean; must_change_password: boolean }
export interface StaffAccess { campus_ids: string[]; roles: StaffRole[]; active: boolean }
export interface StaffList { items: StaffAccount[]; campuses: { campus_id: string }[] }
export interface CreateStaff { username: string; display_name: string; campus_ids: string[]; roles: StaffRole[]; initial_password: string }
export interface StaffReceipt { kind: 'create' | 'reset'; label: string; username: string; password: string }
export interface AccessReceipt { kind: 'access'; label: string; account: StaffAccount }
export type StaffResult = StaffReceipt | AccessReceipt
export interface StaffPending { kind: 'create' | 'reset' | 'access'; label: string; username: string }

const object = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === 'object' && !Array.isArray(value)
const text = (value: unknown): value is string => typeof value === 'string' && value.length > 0 && value.length <= 256
const role = (value: unknown): value is StaffRole => typeof value === 'string' && value in STAFF_ROLES
const ROLE_TEXT: Record<string, string> = { ...STAFF_ROLES, campus_admin: '校区管理员' }
export const roleLabel = (value: string) => ROLE_TEXT[value] ?? value
/** Only sitting-teacher accounts can be reshaped here; another administrator is listed but read-only. */
export const editable = (account: StaffAccount) => account.roles.includes('sitting_teacher') && account.roles.every(role)

export function staffAccount(value: unknown): StaffAccount {
  if (!object(value) || !text(value.principal_id) || !text(value.username) || typeof value.active !== 'boolean' || typeof value.must_change_password !== 'boolean'
    || !Array.isArray(value.roles) || !value.roles.every(text) || value.roles.length > 8 || !Array.isArray(value.campus_ids) || !value.campus_ids.every(text) || value.campus_ids.length > 100)
    throw new AdminError('老师账号信息返回不完整，请刷新核对。')
  return { principal_id: value.principal_id, username: value.username, roles: [...value.roles], campus_ids: [...value.campus_ids], active: value.active, must_change_password: value.must_change_password }
}

export async function listStaff(client: AdminClient): Promise<StaffList> {
  const value = await client.adminRequest<unknown>(staffPath)
  if (!object(value) || !Array.isArray(value.items) || !Array.isArray(value.campuses) || value.items.length > 5000 || value.campuses.length > 1000) throw new AdminError('老师账号列表返回不完整，请稍后刷新。')
  const items = value.items.map(staffAccount)
  const campuses = value.campuses.map(row => { if (!object(row) || !text(row.campus_id)) throw new AdminError('校区信息返回不完整。'); return { campus_id: row.campus_id } })
  if (new Set(items.map(row => row.principal_id)).size !== items.length || new Set(items.map(row => row.username)).size !== items.length) throw new AdminError('老师账号列表包含重复记录，请联系管理员核对。')
  return { items, campuses }
}

export function normalizeStaff(input: CreateStaff): CreateStaff {
  const normalized = { ...input, username: input.username.trim().toLowerCase(), display_name: input.display_name.trim(),
    campus_ids: [...new Set(input.campus_ids)].sort(), roles: [...new Set(input.roles)].sort() as StaffRole[] }
  if (!/^[a-z0-9][a-z0-9._-]{2,63}$/.test(normalized.username)) throw new AdminError('用户名需要 3–64 位英文字母、数字或 . _ -，并以字母或数字开头。')
  if (!normalized.display_name || Array.from(normalized.display_name).length > 80) throw new AdminError('请填写老师姓名，最多 80 个字。')
  if (!normalized.campus_ids.length) throw new AdminError('请至少选择一个已授权校区。')
  if (!normalized.roles.includes('sitting_teacher')) throw new AdminError('老师账号必须包含坐班老师角色。')
  if (!validPassword(input.initial_password)) throw new AdminError('密码需要 15–128 个字符；空格会作为密码的一部分保留。')
  return normalized
}

type Pending = { kind: 'create'; key: string; input: CreateStaff } | { kind: 'reset'; key: string; account: StaffAccount; input: { new_password: string } } | { kind: 'access'; key: string; account: StaffAccount; input: StaffAccess }

/** Same contract as student accounts: secrets and the idempotency key stay in this page's memory, and an
 * unknown outcome is retried with the identical body and key, never inferred from the list. */
export class StaffOperations {
  private pending: Pending | null = null
  private working = false
  private disposed = false
  constructor(private client: AdminClient, private key: () => string = () => crypto.randomUUID()) {}
  get pendingSummary(): StaffPending | null {
    const p = this.pending
    return !p ? null : p.kind === 'create' ? { kind: p.kind, label: p.input.display_name, username: p.input.username } : { kind: p.kind, label: p.account.username, username: p.account.username }
  }
  dispose() { this.disposed = true; this.pending = null }
  discard() { if (this.working) throw new AdminError('请等待当前请求结束。'); this.pending = null }
  async list() { if (this.disposed) throw new AdminError('页面已关闭。'); return listStaff(this.client) }
  async create(input: CreateStaff): Promise<StaffResult> {
    if (this.disposed) throw new AdminError('页面已关闭。')
    if (this.pending) throw new AdminError('上一项操作的结果尚未确认，请先重试或核对。')
    this.pending = { kind: 'create', key: this.key(), input: normalizeStaff(input) }
    return this.retry()
  }
  async reset(account: StaffAccount, password: string): Promise<StaffResult> {
    if (this.disposed) throw new AdminError('页面已关闭。')
    if (this.pending) throw new AdminError('上一项操作的结果尚未确认，请先重试或核对。')
    if (!validPassword(password)) throw new AdminError('密码需要 15–128 个字符；空格会作为密码的一部分保留。')
    this.pending = { kind: 'reset', key: this.key(), account: { ...account }, input: { new_password: password } }
    return this.retry()
  }
  async updateAccess(account: StaffAccount, access: StaffAccess): Promise<StaffResult> {
    if (this.disposed) throw new AdminError('页面已关闭。')
    if (this.pending) throw new AdminError('上一项操作的结果尚未确认，请先重试或核对。')
    if (!editable(account)) throw new AdminError('该账号不是坐班老师账号，不能在此修改权限。')
    const input = { campus_ids: [...new Set(access.campus_ids)].sort(), roles: [...new Set(access.roles)].sort() as StaffRole[], active: access.active }
    if (!input.campus_ids.length) throw new AdminError('请至少保留一个授权校区；不再使用的账号请停用。')
    if (!input.roles.includes('sitting_teacher')) throw new AdminError('老师账号必须包含坐班老师角色。')
    this.pending = { kind: 'access', key: this.key(), account: { ...account }, input }
    return this.retry()
  }
  async retry(): Promise<StaffResult> {
    if (this.disposed || !this.pending) throw new AdminError('当前没有可重试的操作。')
    if (this.working) throw new AdminError('当前操作正在处理，请稍等。')
    this.working = true
    const pending = this.pending
    try {
      const endpoint = pending.kind === 'create' ? staffPath : `${staffPath}/${encodeURIComponent(pending.account.principal_id)}/${pending.kind === 'reset' ? 'reset-password' : 'access'}`
      const value = await this.client.adminRequest<unknown>(endpoint, { method: 'POST', body: { ...pending.input }, idempotencyKey: pending.key })
      if (this.disposed) throw new AdminError('页面已关闭，请重新登录后核对账号。')
      let receipt: StaffResult
      if (pending.kind === 'create') {
        const created = staffAccount(value)
        if (created.username !== pending.input.username || !created.must_change_password || created.campus_ids.join() !== pending.input.campus_ids.join()) throw new AdminError('创建结果与提交信息不一致，请核对老师账号列表。')
        receipt = { kind: 'create', label: pending.input.display_name, username: created.username, password: pending.input.initial_password }
      } else if (pending.kind === 'access') {
        const updated = staffAccount(value)
        if (updated.principal_id !== pending.account.principal_id || updated.active !== pending.input.active || updated.campus_ids.join() !== pending.input.campus_ids.join() || [...updated.roles].sort().join() !== pending.input.roles.join())
          throw new AdminError('授权修改结果与提交不一致，请重试原操作或刷新核对。')
        receipt = { kind: 'access', label: pending.account.username, account: updated }
      } else {
        const reset = value as { principal_id?: unknown; must_change_password?: unknown } | null
        if (!reset || reset.principal_id !== pending.account.principal_id || reset.must_change_password !== true) throw new AdminError('重置结果尚未得到确认，请重试原操作。')
        receipt = { kind: 'reset', label: pending.account.username, username: pending.account.username, password: pending.input.new_password }
      }
      this.pending = null
      return receipt
    } finally { this.working = false }
  }
}
