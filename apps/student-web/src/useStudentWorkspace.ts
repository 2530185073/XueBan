import { useCallback, useEffect, useRef, useState } from 'react'
import { createStudentClient, remoteDraftId } from './core'
import { pairingFromHash, type CorrectionTarget } from './presentation'
import type { CaseDetail, DraftSummary, DraftView, Hint, HomeworkDetail, HomeworkSession, IdentityContext } from './core/types'

export function useStudentWorkspace() {
  const [{ client, initialPair }] = useState(() => {
    const client = createStudentClient()
    const code = pairingFromHash(window.location.hash)
    // pair synchronously journals its key/code before the fragment disappears.
    const initialPair = code ? client.pair(code).then(identity => ({ identity }), error => ({ error })) : null
    if (new URLSearchParams(window.location.hash.slice(1)).has('pair')) window.history.replaceState(window.history.state, '', window.location.pathname + window.location.search)
    return { client, initialPair }
  })
  const [identity, setIdentity] = useState<IdentityContext | null>(null)
  const [studentId, setStudentId] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState('')
  const busyRef = useRef(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [drafts, setDrafts] = useState<DraftSummary[]>([])
  const [history, setHistory] = useState<HomeworkSession[]>([])
  const [cursor, setCursor] = useState<string | null>(null)
  const [draft, setDraft] = useState<DraftView | null>(null)
  const [detail, setDetail] = useState<HomeworkDetail | null>(null)
  const [hint, setHint] = useState<Hint | null>(null)
  const activeDraft = useRef<string | null>(null)
  const syncSequence = useRef(0)

  const clearWork = useCallback(() => { ++syncSequence.current; activeDraft.current = null; setDraft(null); setDetail(null); setDrafts([]); setHistory([]); setCursor(null); setHint(null) }, [])
  const fail = useCallback((failure: unknown) => {
    setError(failure instanceof Error ? failure.message : '暂时没有连接上，请稍后再试。')
    setIdentity(client.identity); setStudentId(client.selectedStudentId)
    if (!client.identity) clearWork()
  }, [client, clearWork])
  const sync = useCallback(async () => {
    const sequence = ++syncSequence.current
    if (!client.identity) { setIdentity(null); setStudentId(null); clearWork(); return }
    const list = await client.listDrafts()
    const currentId = activeDraft.current
    const current = currentId ? await client.getDraft(currentId) : null
    if (sequence !== syncSequence.current) return
    setDrafts(list)
    if (current && currentId === activeDraft.current) { setDraft(current); setDetail(current.detail) }
  }, [client, clearWork])
  useEffect(() => {
    let live = true
    const restore = async () => {
      const paired = initialPair ? await initialPair : null
      if (paired && 'error' in paired) throw paired.error
      return paired && 'identity' in paired ? paired.identity : await client.restore()
    }
    void restore().then(async (me) => { if (!live) return; setIdentity(me); setStudentId(client.selectedStudentId); if (me) await sync() }).catch((e: unknown) => { if (live) fail(e) }).finally(() => { if (live) setLoading(false) })
    const unsubscribe = client.subscribe(() => { void sync().catch(fail) })
    return () => { live = false; unsubscribe() }
  }, [client, initialPair, fail, sync])
  const action = useCallback(async (label: string, task: () => Promise<void>) => {
    if (busyRef.current) return
    busyRef.current = true; setBusy(label); setError(null); setNotice(null)
    try { await task() } catch (e) { fail(e) } finally { busyRef.current = false; setBusy('') }
  }, [fail])
  const selectDraft = useCallback(async (id: string) => { activeDraft.current = id; const next = await client.getDraft(id); setDraft(next); setDetail(next.detail); setHint(null) }, [client])
  return {
    client, identity, studentId, loading, busy, error, notice, drafts, history, cursor, draft, detail, hint,
    clearError: () => setError(null),
    pair: (code: string) => action('正在配对', async () => { const me = await client.pair(code); setIdentity(me); setStudentId(client.selectedStudentId); clearWork(); await sync(); setNotice('配对好了，先确认自己的名字，再开始今天的作业。') }),
    reconnect: () => action('正在恢复连接', async () => { const me = await client.restore(); setIdentity(me); setStudentId(client.selectedStudentId); if (me) await sync(); else setNotice('请向老师获取新的配对码。') }),
    logout: () => { if (busyRef.current) return; client.disconnect(); setIdentity(null); setStudentId(null); clearWork(); setError(null); setNotice(null) },
    selectStudent: (id: string) => action('切换作业本', async () => { client.selectStudent(id); setStudentId(client.selectedStudentId); clearWork(); await sync() }),
    newWork: () => { if (busyRef.current) return; activeDraft.current = null; setDraft(null); setDetail(null); setHint(null); setError(null); setNotice(null) },
    importPhoto: (file: File, correction?: CorrectionTarget) => action('正在准备照片', async () => { const next = correction ? await client.createCorrection(correction.caseId, file, 'unknown', correction.parentAttemptId) : await client.createDraft(file); activeDraft.current = next.id; setDraft(next); setDetail(next.detail); setHint(null); await sync() }),
    openDraft: (id: string) => action('正在打开作业', () => selectDraft(id)),
    run: () => action('正在检查作业', async () => { if (!draft) return; const next = await client.runDraft(draft.id, { retryFailed: draft.stage === 'failed' }); setDraft(next); setDetail(next.detail); await sync() }),
    refreshDetail: () => action('正在刷新结果', async () => { const homeworkId = detail?.homework.id ?? draft?.homeworkId; if (!homeworkId) return; const next = await client.openHomework(homeworkId); setDetail(next); await sync(); setDetail(next) }),
    loadHistory: (more = false) => action('正在读取历史', async () => { const page = await client.history(more ? cursor ?? undefined : undefined); setHistory((old) => more ? [...old, ...page.items.filter(item => !old.some(existing => existing.id === item.id))] : page.items); setCursor(page.next_cursor); await sync() }),
    openHomework: (id: string) => action('正在打开历史', async () => { const opened = await client.openHomework(id); await selectDraft(opened.draftId ?? remoteDraftId(id)); await sync() }),
    requestHint: (row: CaseDetail, level: 'direction' | 'key_step') => action('正在想一个提示', async () => { setHint(await client.requestHint(row.case.id, level)); await sync() }),
    hintViewed: (value: Hint) => { void client.recordHintViewed(value.id).then(() => sync()).catch(fail) },
    requestHelp: (row: CaseDetail, recognition = false) => action('正在请老师帮助', async () => { await client.requestHelp(row.case.id, recognition ? 'recognition' : 'question', recognition ? '识别文字与照片不一致，请老师核对。' : '我需要老师帮我看看这道题。'); setNotice('求助已提交，等坐班老师过来一起看。'); await sync() }),
  }
}
export type Workspace = ReturnType<typeof useStudentWorkspace>
