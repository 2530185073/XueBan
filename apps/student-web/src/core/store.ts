import type { JournalEntry, StoredDraft } from './types'
import { StudentError } from './errors'

export const LOCAL_TTL_MS = 7 * 24 * 60 * 60 * 1000
export interface PurgeReport { drafts: number; operations: number; expiredPending: number }
export interface DeviceNotice { scope: string; expiredPending: number; at: string }
const key = (scope: string, id: string) => JSON.stringify([scope, id])
const STORES = ['drafts', 'operations', 'notices'] as const
export class DraftStore {
  private db: Promise<IDBDatabase>
  constructor(factory: IDBFactory = indexedDB, name = 'xueban-student-v1') {
    this.db = new Promise((resolve, reject) => {
      const request = factory.open(name, 2)
      request.onupgradeneeded = () => { for (const store of STORES) if (!request.result.objectStoreNames.contains(store)) request.result.createObjectStore(store) }
      request.onsuccess = () => { request.result.onversionchange = () => request.result.close(); resolve(request.result) }
      request.onerror = () => reject(new StudentError('storage_unavailable', 0, '浏览器无法保存草稿，请允许网站存储。'))
      request.onblocked = () => reject(new StudentError('storage_blocked', 0, '请关闭其他学伴页面后重试。'))
    })
  }
  private async read<T>(store: string, recordKey?: string): Promise<T> {
    const db = await this.db
    return new Promise((resolve, reject) => {
      const tx = db.transaction(store, 'readonly'); const os = tx.objectStore(store)
      const request = recordKey === undefined ? os.getAll() : os.get(recordKey)
      request.onsuccess = () => resolve(request.result as T)
      request.onerror = () => reject(new StudentError('storage_read'))
    })
  }
  private async write(store: string, recordKey: string, value?: unknown): Promise<void> {
    const db = await this.db
    return new Promise((resolve, reject) => {
      const tx = db.transaction(store, 'readwrite')
      if (value === undefined) tx.objectStore(store).delete(recordKey); else tx.objectStore(store).put(value, recordKey)
      tx.oncomplete = () => resolve(); tx.onabort = tx.onerror = () => reject(new StudentError('storage_write', 0, '草稿保存失败，请检查本机剩余空间。'))
    })
  }
  // Reads every store in one readwrite transaction, then lets `apply` issue
  // deletes/puts from the success callback so the transaction stays active.
  private async sweep<T>(apply: (rows: Record<(typeof STORES)[number], { key: IDBValidKey; value: unknown }[]>, os: Record<(typeof STORES)[number], IDBObjectStore>) => T): Promise<T> {
    const db = await this.db
    return new Promise((resolve, reject) => {
      const tx = db.transaction([...STORES], 'readwrite')
      const os = { drafts: tx.objectStore('drafts'), operations: tx.objectStore('operations'), notices: tx.objectStore('notices') }
      const rows = { drafts: [], operations: [], notices: [] } as Record<(typeof STORES)[number], { key: IDBValidKey; value: unknown }[]>
      let remaining = STORES.length * 2, result: T
      const values: Partial<Record<string, unknown[]>> = {}, keys: Partial<Record<string, IDBValidKey[]>> = {}
      const done = () => {
        if (--remaining) return
        for (const store of STORES) rows[store] = (keys[store] ?? []).map((k, index) => ({ key: k, value: values[store]?.[index] }))
        result = apply(rows, os)
      }
      for (const store of STORES) {
        const all = os[store].getAll(), allKeys = os[store].getAllKeys()
        all.onsuccess = () => { values[store] = all.result; done() }
        allKeys.onsuccess = () => { keys[store] = allKeys.result; done() }
      }
      tx.oncomplete = () => resolve(result); tx.onabort = tx.onerror = () => reject(new StudentError('storage_write', 0, '本机草稿清理未完成，请稍后重试。'))
    })
  }
  async draft(scope: string, id: string) { const d = await this.read<StoredDraft | undefined>('drafts', key(scope, id)); if (!d || d.scope !== scope) throw new StudentError('draft_not_found', 404); return d }
  async drafts(scope: string) { return (await this.read<StoredDraft[]>('drafts')).filter(d => d.scope === scope).sort((a, b) => b.updatedAt.localeCompare(a.updatedAt)) }
  saveDraft(draft: StoredDraft) { return this.write('drafts', key(draft.scope, draft.id), draft) }
  async operation(scope: string, id: string) { return this.read<JournalEntry | undefined>('operations', key(scope, id)) }
  async reserve(entry: JournalEntry): Promise<JournalEntry> {
    const db = await this.db
    return new Promise((resolve, reject) => {
      const tx = db.transaction('operations', 'readwrite'), os = tx.objectStore('operations')
      const request = os.get(key(entry.scope, entry.id)); let selected = entry
      request.onsuccess = () => { selected = request.result ?? entry; if (!request.result) os.put(entry, key(entry.scope, entry.id)) }
      tx.oncomplete = () => resolve(selected); tx.onerror = tx.onabort = () => reject(new StudentError('journal_unavailable'))
    })
  }
  saveOperation(entry: JournalEntry) { return this.write('operations', key(entry.scope, entry.id), { ...entry, updatedAt: new Date().toISOString() }) }
  removeOperation(scope: string, id: string) { return this.write('operations', key(scope, id)) }
  async hasPending(scope: string, prefix: string) { return (await this.read<JournalEntry[]>('operations')).some(e => e.scope === scope && e.id.startsWith(prefix) && e.response === undefined) }
  async pending(scope: string, prefix: string) { return (await this.read<JournalEntry[]>('operations')).filter(e => e.scope === scope && e.id.startsWith(prefix) && e.response === undefined) }
  /**
   * Drops drafts and journal entries untouched for `ttl`. A pending (unknown
   * outcome) write keeps its draft alive until the write itself expires; each
   * expired pending write leaves a per-scope notice for the next sign-in.
   * Entries written before timestamps existed start their clock now.
   */
  purgeExpired(now = Date.now(), ttl = LOCAL_TTL_MS): Promise<PurgeReport> {
    const stamp = new Date(now).toISOString(), old = (value: unknown) => { const time = typeof value === 'string' ? Date.parse(value) : NaN; return Number.isFinite(time) && now - time > ttl }
    return this.sweep((rows, os) => {
      const report: PurgeReport = { drafts: 0, operations: 0, expiredPending: 0 }, expired = new Map<string, number>(), alive: JournalEntry[] = []
      for (const { key: k, value } of rows.operations) {
        const op = value as JournalEntry
        if (!op || typeof op.scope !== 'string') { os.operations.delete(k); report.operations++; continue }
        if (!op.updatedAt || !Number.isFinite(Date.parse(op.updatedAt))) { os.operations.put({ ...op, updatedAt: stamp }, k); alive.push(op); continue }
        if (!old(op.updatedAt)) { alive.push(op); continue }
        os.operations.delete(k); report.operations++
        if (op.response === undefined) { report.expiredPending++; expired.set(op.scope, (expired.get(op.scope) ?? 0) + 1) }
      }
      for (const { key: k, value } of rows.drafts) {
        const d = value as StoredDraft
        if (!d || typeof d.scope !== 'string') { os.drafts.delete(k); report.drafts++; continue }
        if (!old(d.updatedAt)) continue
        const prefix = `${d.journalId ?? d.id}:`, related = alive.filter(op => op.scope === d.scope && op.id.startsWith(prefix))
        if (related.some(op => op.response === undefined)) continue
        os.drafts.delete(k); report.drafts++
        for (const op of related) { os.operations.delete(key(op.scope, op.id)); report.operations++ }
      }
      for (const { key: k, value } of rows.notices) { const notice = value as DeviceNotice | undefined; if (!notice || old(notice.at) && !expired.has(notice.scope)) os.notices.delete(k) }
      for (const [scope, count] of expired) {
        const previous = rows.notices.find(row => row.key === scope)?.value as DeviceNotice | undefined
        os.notices.put({ scope, expiredPending: (previous?.expiredPending ?? 0) + count, at: stamp } satisfies DeviceNotice, scope)
      }
      return report
    })
  }
  /** Removes drafts (with their photos), journal entries and notices of every matching scope. */
  purgeScopes(matches: (scope: string) => boolean): Promise<PurgeReport> {
    return this.sweep((rows, os) => {
      const report: PurgeReport = { drafts: 0, operations: 0, expiredPending: 0 }
      for (const { key: k, value } of rows.operations) { const op = value as JournalEntry | undefined; if (!op || matches(op.scope)) { os.operations.delete(k); report.operations++; if (op && op.response === undefined) report.expiredPending++ } }
      for (const { key: k, value } of rows.drafts) { const d = value as StoredDraft | undefined; if (!d || matches(d.scope)) { os.drafts.delete(k); report.drafts++ } }
      for (const { key: k, value } of rows.notices) { const notice = value as DeviceNotice | undefined; if (!notice || matches(notice.scope)) os.notices.delete(k) }
      return report
    })
  }
  async takeNotice(scope: string): Promise<DeviceNotice | undefined> {
    const notice = await this.read<DeviceNotice | undefined>('notices', scope)
    if (notice) await this.write('notices', scope)
    return notice
  }
}
