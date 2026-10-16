import type { AccountList, AdminClient, StudentAccount } from './types'

const path = '/v1/admin/student-accounts'
const object = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === 'object' && !Array.isArray(value)
const text = (value: unknown): value is string => typeof value === 'string' && value.length > 0 && value.length <= 256
export class AdminError extends Error { constructor(message: string) { super(message); this.name = 'AdminError' } }
export function account(value: unknown): StudentAccount {
  if (!object(value) || !['student_id', 'student_name', 'username', 'campus_id', 'principal_id'].every(key => text(value[key])) || typeof value.active !== 'boolean' || typeof value.must_change_password !== 'boolean') throw new AdminError('账号信息返回不完整，请刷新核对。')
  // Only declared account fields reach the page; never render an entire response.
  return { student_id: value.student_id as string, student_name: value.student_name as string, username: value.username as string, campus_id: value.campus_id as string, principal_id: value.principal_id as string, active: value.active, must_change_password: value.must_change_password }
}
export async function listAccounts(client: AdminClient): Promise<AccountList> {
  const value = await client.adminRequest<unknown>(path)
  if (!object(value) || !Array.isArray(value.items) || !Array.isArray(value.campuses) || value.items.length > 10000 || value.campuses.length > 1000) throw new AdminError('账号列表返回不完整，请稍后刷新。')
  const items = value.items.map(account)
  const campuses = value.campuses.map(row => { if (!object(row) || !text(row.campus_id)) throw new AdminError('校区信息返回不完整。'); return { campus_id: row.campus_id } })
  if (new Set(items.map(row => row.username)).size !== items.length || new Set(items.map(row => row.student_id)).size !== items.length || new Set(campuses.map(row => row.campus_id)).size !== campuses.length) throw new AdminError('账号列表包含重复记录，请联系管理员核对。')
  return { items, campuses }
}
export function errorMessage(error: unknown): string { return error instanceof Error && error.message ? error.message : '本次操作未完成，请核对网络后重试原操作。' }
export const accountsPath = path
