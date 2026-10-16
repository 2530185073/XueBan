import type { JournalEntry, StoredDraft } from './types'
import { StudentError } from './errors'

const key = (scope: string, id: string) => JSON.stringify([scope, id])
export class DraftStore {
  private db: Promise<IDBDatabase>
  constructor(factory: IDBFactory = indexedDB, name = 'xueban-student-v1') {
    this.db = new Promise((resolve, reject) => {
      const request = factory.open(name, 1)
      request.onupgradeneeded = () => { request.result.createObjectStore('drafts'); request.result.createObjectStore('operations') }
      request.onsuccess = () => resolve(request.result)
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
  saveOperation(entry: JournalEntry) { return this.write('operations', key(entry.scope, entry.id), entry) }
  removeOperation(scope: string, id: string) { return this.write('operations', key(scope, id)) }
  async hasPending(scope: string, prefix: string) { return (await this.read<JournalEntry[]>('operations')).some(e => e.scope === scope && e.id.startsWith(prefix) && e.response === undefined) }
  async pending(scope: string, prefix: string) { return (await this.read<JournalEntry[]>('operations')).filter(e => e.scope === scope && e.id.startsWith(prefix) && e.response === undefined) }
}
