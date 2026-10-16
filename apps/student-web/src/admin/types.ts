export interface AdminClient {
  adminRequest<T>(path: string, options?: { method?: 'GET' | 'POST'; body?: unknown; idempotencyKey?: string }): Promise<T>
}
export interface StudentAccount {
  student_id: string
  student_name: string
  username: string
  active: boolean
  must_change_password: boolean
  campus_id: string
  principal_id: string
}
export interface AccountList { items: StudentAccount[]; campuses: { campus_id: string }[] }
export interface CreateAccount { username: string; student_name: string; campus_id: string; initial_password: string }
export interface CredentialReceipt { kind: 'create' | 'reset'; studentName: string; username: string; password: string }
export interface PendingSummary { kind: 'create' | 'reset'; studentName: string; username: string }
