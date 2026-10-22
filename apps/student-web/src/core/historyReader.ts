import type { HomeworkPage, HomeworkSession } from './types'
export interface HistoryView { items: HomeworkSession[]; cursor: string | null; loading: boolean; loaded: boolean }
export const emptyHistoryView = (): HistoryView => ({ items: [], cursor: null, loading: false, loaded: false })
interface HistoryClient { readonly historyScope: string | null; history(cursor?: string): Promise<HomeworkPage> }
/** Read-only history has its own queue; it never acquires the mutation lock. */
export class HistoryReader {
  private sequence = 0
  private value = emptyHistoryView()
  constructor(private client: HistoryClient) {}
  reset() { ++this.sequence; this.value = emptyHistoryView(); return this.value }
  async load(more: boolean, update: (view: HistoryView) => void): Promise<HomeworkSession[] | null> {
    const scope = this.client.historyScope
    if (!scope) return null
    const sequence = ++this.sequence, before = this.value
    const current = () => sequence === this.sequence && this.client.historyScope === scope
    this.value = { ...before, loading: true }; update(this.value)
    try {
      const page = await this.client.history(more ? before.cursor ?? undefined : undefined)
      if (!current()) return null
      const items = more ? [...before.items, ...page.items.filter(item => !before.items.some(old => old.id === item.id))] : page.items
      this.value = { items, cursor: page.next_cursor, loaded: true, loading: false }; update(this.value)
      return items
    } catch (error) { if (current()) throw error; return null }
    finally { if (current() && this.value.loading) { this.value = { ...this.value, loading: false }; update(this.value) } }
  }
}
