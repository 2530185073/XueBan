import { useCallback, useEffect, useRef, useState } from 'react'
import { createStudentClient, remoteDraftId, safeError } from './core'
import { type CorrectionTarget } from './presentation'
import type { Account, CaseDetail, CreateHelp, DraftSummary, DraftView, Hint, HomeworkDetail, HomeworkSession, HomeworkSummary, IdentityContext } from './core/types'

const helpDefaults: Record<CreateHelp['kind'], string> = { question: '我需要老师帮我看看这道题。', recognition: '识别文字与照片不一致，请老师核对。', judgment: '这道题的判断我看不懂，请老师看看。', appeal: '我觉得这道题的判断可能不对，请老师重新看看。' }
const helpNotices: Record<CreateHelp['kind'], string> = { question: '求助已提交，等坐班老师过来一起看。', recognition: '已请老师核对识别结果。', judgment: '已请老师看看这次判断。', appeal: '重查申请已提交。老师处理前，当前结果保持不变。' }

export function useStudentWorkspace() {
  const [client] = useState(() => {
    // Clear a retired pairing fragment without redeeming or persisting it.
    if (new URLSearchParams(window.location.hash.slice(1)).has('pair')) window.history.replaceState(window.history.state, '', window.location.pathname + window.location.search)
    return createStudentClient()
  })
  const [account, setAccount] = useState<Account | null>(null)
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
  const [summaries, setSummaries] = useState<Record<string, HomeworkSummary | undefined>>({})
  const [summaryFailed, setSummaryFailed] = useState<string[]>([])
  // Bumped after any write or refresh so case records re-read from the server.
  const [recordVersion, setRecordVersion] = useState(0)
  const activeDraft = useRef<string | null>(null)
  const syncSequence = useRef(0)
  const summaryRun = useRef(0)

  const clearWork = useCallback(() => { ++syncSequence.current; ++summaryRun.current; activeDraft.current = null; setDraft(null); setDetail(null); setDrafts([]); setHistory([]); setCursor(null); setHint(null); setSummaries({}); setSummaryFailed([]) }, [])
  const fail = useCallback((failure: unknown) => {
    setError(safeError(failure).message)
    setAccount(client.account); setIdentity(client.identity); setStudentId(client.selectedStudentId)
    if (!client.identity) clearWork()
  }, [client, clearWork])
  const sync = useCallback(async () => {
    const sequence = ++syncSequence.current
    setAccount(client.account); setIdentity(client.identity); setStudentId(client.selectedStudentId)
    if (!client.identity || !client.selectedStudentId) { clearWork(); return }
    const list = await client.listDrafts()
    const currentId = activeDraft.current
    const current = currentId ? await client.getDraft(currentId) : null
    if (sequence !== syncSequence.current) return
    setDrafts(list)
    if (current && currentId === activeDraft.current) { setDraft(current); setDetail(current.detail) }
  }, [client, clearWork])
  const deviceNotice = useCallback(async () => { if (!client.selectedStudentId) return; const text = await client.takeDeviceNotice().catch(() => null); if (text) setNotice(text) }, [client])
  useEffect(() => {
    let live = true
    void client.restore().then(async () => { if (live) { await sync(); await deviceNotice() } }).catch((e: unknown) => { if (live) fail(e) }).finally(() => { if (live) setLoading(false) })
    const unsubscribe = client.subscribe(() => { void sync().catch(fail) })
    return () => { live = false; unsubscribe() }
  }, [client, deviceNotice, fail, sync])
  const action = useCallback(async (label: string, task: () => Promise<void>) => {
    if (busyRef.current) return false
    busyRef.current = true; setBusy(label); setError(null); setNotice(null)
    try { await task(); return true } catch (e) { fail(e); return false } finally { busyRef.current = false; setBusy('') }
  }, [fail])
  const selectDraft = useCallback(async (id: string) => { activeDraft.current = id; const next = await client.getDraft(id); setDraft(next); setDetail(next.detail); setHint(null) }, [client])
  // Background, read-only and cancelled by sign-out; one failing homework does not hide the others.
  const loadSummaries = useCallback((items: HomeworkSession[], known: Record<string, HomeworkSummary | undefined>) => {
    const run = ++summaryRun.current, queue = items.filter(h => known[h.id]?.revision !== h.revision)
    if (!queue.length) return
    void client.homeworkSummaries(queue, (id, summary) => {
      if (run !== summaryRun.current) return
      if (summary) { setSummaries(old => ({ ...old, [id]: summary })); setSummaryFailed(old => old.filter(other => other !== id)) }
      else setSummaryFailed(old => old.includes(id) ? old : [...old, id])
    }).catch(() => { if (run === summaryRun.current) setSummaryFailed(old => [...new Set([...old, ...queue.map(h => h.id)])]) })
  }, [client])
  const refreshOpened = useCallback(async () => {
    const homeworkId = detail?.homework.id ?? draft?.homeworkId; if (!homeworkId) return
    const next = await client.openHomework(homeworkId); setDetail(next); await sync(); setDetail(next); setRecordVersion(v => v + 1)
  }, [client, detail?.homework.id, draft?.homeworkId, sync])
  return {
    client, account, identity, studentId, loading, busy, error, notice, drafts, history, cursor, draft, detail, hint, summaries, summaryFailed, recordVersion,
    clearError: () => setError(null),
    login: (username: string, password: string) => action('正在登录', async () => { clearWork(); await client.login(username, password); await sync(); await deviceNotice() }),
    changePassword: (currentPassword: string, newPassword: string) => action('正在保存新密码', async () => { await client.changePassword(currentPassword, newPassword); await sync(); setNotice('密码已更新，以后用新密码登录同一个作业本。') }),
    reconnect: () => action('正在恢复连接', async () => { await client.restore(); await sync(); await deviceNotice() }),
    logout: () => action('正在退出登录', async () => { try { await client.logout() } finally { clearWork() } await sync(); setError(null); setNotice(null) }),
    newWork: () => { if (busyRef.current) return; activeDraft.current = null; setDraft(null); setDetail(null); setHint(null); setError(null); setNotice(null) },
    importPhoto: (file: File, correction?: CorrectionTarget) => action('正在准备照片', async () => { const next = correction ? await client.createCorrection(correction.caseId, file, 'unknown', correction.parentAttemptId) : await client.createDraft(file); activeDraft.current = next.id; setDraft(next); setDetail(next.detail); setHint(null); await sync() }),
    openDraft: (id: string) => action('正在打开作业', () => selectDraft(id)),
    run: () => action('正在检查作业', async () => { if (!draft) return; const next = await client.runDraft(draft.id, { retryFailed: draft.stage === 'failed' }); setDraft(next); setDetail(next.detail); setRecordVersion(v => v + 1); await sync() }),
    refreshDetail: () => action('正在刷新结果', refreshOpened),
    loadHistory: (more = false) => action('正在读取历史', async () => {
      const page = await client.history(more ? cursor ?? undefined : undefined)
      const merged = more ? [...history, ...page.items.filter(item => !history.some(existing => existing.id === item.id))] : page.items
      setHistory(merged); setCursor(page.next_cursor); await sync(); loadSummaries(merged, more ? summaries : {})
    }),
    openHomework: (id: string) => action('正在打开历史', async () => { const opened = await client.openHomework(id); await selectDraft(opened.draftId ?? remoteDraftId(id)); setRecordVersion(v => v + 1); await sync() }),
    requestHint: (row: CaseDetail, level: 'direction' | 'key_step') => action('正在想一个提示', async () => { setHint(await client.requestHint(row.case.id, level)); setRecordVersion(v => v + 1); await sync() }),
    hintViewed: (value: Hint) => { void client.recordHintViewed(value.id).then(() => { setRecordVersion(v => v + 1); return sync() }).catch(fail) },
    requestHelp: (row: CaseDetail, kind: CreateHelp['kind'] = 'question', note?: string) => action(kind === 'appeal' ? '正在申请老师重查' : '正在请老师帮助', async () => {
      await client.requestHelp(row.case.id, kind, note?.trim() || helpDefaults[kind])
      setNotice(helpNotices[kind])
      // The request is already saved; a failed re-read only delays the status card.
      try { await refreshOpened() } catch { setRecordVersion(v => v + 1) }
    }),
    clearLocalData: () => action('正在清除本机草稿', async () => {
      const report = await client.clearLocalData()
      activeDraft.current = null; setDraft(null); setDetail(null); setHint(null); await sync()
      setNotice(`已从这台设备清除 ${report.drafts} 份草稿和 ${report.operations} 条提交记录${report.expiredPending ? `（其中 ${report.expiredPending} 条结果尚未确认）` : ''}。服务器上的作业不受影响。`)
    }),
  }
}
export type Workspace = ReturnType<typeof useStudentWorkspace>
