import { useCallback, useEffect, useRef, useState } from 'react'
import { createStudentClient, remoteDraftId, safeError, type EditChange } from './core'
import { type CorrectionTarget } from './presentation'
import type { Account, CaseDetail, CreateHelp, DraftSummary, DraftView, Hint, HomeworkDetail, HomeworkSession, HomeworkSummary, IdentityContext } from './core/types'
import type { StudentCurriculum } from './core/learning'
import type { CurriculumContext } from './core/student'

const helpDefaults: Record<CreateHelp['kind'], string> = { question: '我需要老师帮我看看这道题。', recognition: '识别文字与照片不一致，请老师核对。', judgment: '这道题的判断我看不懂，请老师看看。', appeal: '我觉得这道题的判断可能不对，请老师重新看看。' }
const helpNotices: Record<CreateHelp['kind'], string> = { question: '求助已提交，可以先继续其他题。', recognition: '已反馈文字与照片不一致，老师会帮你核对。可以先继续其他题。', judgment: '已请老师看看这次判断，可以先继续其他题。', appeal: '重查申请已提交。老师处理前，当前结果保持不变，可以先继续其他题。' }

export function useStudentWorkspace() {
  const [client] = useState(() => {
    // Clear a retired pairing fragment without redeeming or persisting it.
    if (new URLSearchParams(window.location.hash.slice(1)).has('pair')) window.history.replaceState(window.history.state, '', window.location.pathname + window.location.search)
    return createStudentClient()
  })
  const [account, setAccount] = useState<Account | null>(null)
  const [identity, setIdentity] = useState<IdentityContext | null>(null)
  const [studentId, setStudentId] = useState<string | null>(null)
  const [studentCurriculum, setStudentCurriculum] = useState<StudentCurriculum | null>(null)
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
  const autoPollTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const autoPollRunning = useRef(false)

  const clearWork = useCallback(() => { ++syncSequence.current; ++summaryRun.current; activeDraft.current = null; setDraft(null); setDetail(null); setDrafts([]); setHistory([]); setCursor(null); setHint(null); setSummaries({}); setSummaryFailed([]); setStudentCurriculum(null) }, [])
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
  const refreshCurriculum = useCallback(async () => { if (!client.selectedStudentId) { setStudentCurriculum(null); return }; setStudentCurriculum(await client.studentCurriculum()) }, [client])
  const action = useCallback(async (label: string, task: () => Promise<void>) => {
    if (busyRef.current) return false
    busyRef.current = true; setBusy(label); setError(null); setNotice(null)
    try { await task(); return true } catch (e) { fail(e); return false } finally { busyRef.current = false; setBusy('') }
  }, [fail])
  // The open draft id per student for this tab only, so a reload lands back on the same work.
  const remember = useCallback((id: string | null) => {
    const student = client.selectedStudentId; if (!student) return
    try { if (id) sessionStorage.setItem(`xueban:active-draft:${student}`, id); else sessionStorage.removeItem(`xueban:active-draft:${student}`) } catch { /* Only a convenience after reload. */ }
  }, [client])
  const show = useCallback((next: DraftView | null) => { activeDraft.current = next?.id ?? null; remember(next?.id ?? null); setDraft(next); setDetail(next?.detail ?? null); setHint(null) }, [remember])
  const selectDraft = useCallback(async (id: string) => { activeDraft.current = id; show(await client.getDraft(id)) }, [client, show])
  /** After reload or sign-in: reopen the remembered draft, with server progress when the server already has it. */
  const resumeActive = useCallback(async () => {
    const student = client.selectedStudentId; if (!student || activeDraft.current) return
    let id: string | null = null
    try { id = sessionStorage.getItem(`xueban:active-draft:${student}`) } catch { return }
    if (!id) return
    try {
      const local = await client.getDraft(id)
      if (local.homeworkId && !local.correction && !local.editable) { try { const opened = await client.openHomework(local.homeworkId); await selectDraft(opened.draftId ?? id); return } catch { /* Offline: show what this device has. */ } }
      if (!activeDraft.current) show(local)
    } catch { remember(null) }
  }, [client, remember, selectDraft, show])
  useEffect(() => {
    let live = true
    void client.restore().then(async () => { if (live) { await sync(); await deviceNotice(); await refreshCurriculum(); await resumeActive() } }).catch((e: unknown) => { if (live) fail(e) }).finally(() => { if (live) setLoading(false) })
    const unsubscribe = client.subscribe(() => { void sync().catch(fail) })
    return () => { live = false; unsubscribe() }
  }, [client, deviceNotice, fail, refreshCurriculum, resumeActive, sync])
  const [online, setOnline] = useState(() => typeof navigator === 'undefined' || navigator.onLine !== false)
  useEffect(() => {
    const update = () => setOnline(navigator.onLine !== false)
    window.addEventListener('online', update); window.addEventListener('offline', update)
    return () => { window.removeEventListener('online', update); window.removeEventListener('offline', update) }
  }, [])
  // A bounded client run returns a waiting draft when work is still active;
  // keep observing that draft while it remains open in this page.
  useEffect(() => {
    const id = draft?.id
    const activeStage = !!id && ['waiting', 'recognizing', 'checking'].includes(draft.stage)
    if (!id || !activeStage || !identity || !studentId) {
      if (autoPollTimer.current) { clearTimeout(autoPollTimer.current); autoPollTimer.current = null }
      return
    }
    let live = true
    const schedule = () => {
      if (!live || autoPollTimer.current || activeDraft.current !== id) return
      autoPollTimer.current = setTimeout(() => { autoPollTimer.current = null; void poll() }, autoPollRunning.current ? 250 : 1200)
    }
    const poll = async () => {
      if (!live || activeDraft.current !== id || !identity || !studentId) return
      // Switching drafts while an old poll finishes must not consume the new
      // draft's only timer. Keep observing it after the in-flight run exits.
      if (autoPollRunning.current || busyRef.current) { schedule(); return }
      autoPollRunning.current = true
      try {
        const next = await client.runDraft(id)
        if (!live || activeDraft.current !== id) return
        setDraft(next); setDetail(next.detail); setRecordVersion(version => version + 1)
      } catch (e) {
        if (live && activeDraft.current === id) fail(e)
      } finally {
        autoPollRunning.current = false
        schedule()
      }
    }
    schedule()
    return () => {
      live = false
      if (autoPollTimer.current) { clearTimeout(autoPollTimer.current); autoPollTimer.current = null }
    }
  }, [client, draft?.id, draft?.stage, fail, identity, studentId])
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
    client, account, identity, studentId, studentCurriculum, loading, busy, error, notice, drafts, history, cursor, draft, detail, hint, summaries, summaryFailed, recordVersion, online,
    clearError: () => setError(null),
    showNotice: (text: string | null) => setNotice(text),
    /** Learning panels handle their own errors; one that signed the account out is shown like any other. */
    report: (failure: unknown) => { if (!client.identity) fail(failure) },
    login: (username: string, password: string) => action('正在登录', async () => { clearWork(); await client.login(username, password); await sync(); await deviceNotice(); await refreshCurriculum(); await resumeActive() }),
    changePassword: (currentPassword: string, newPassword: string) => action('正在保存新密码', async () => { await client.changePassword(currentPassword, newPassword); await sync(); setNotice('密码已更新，以后用新密码登录同一个作业本。') }),
    reconnect: () => action('正在恢复连接', async () => { await client.restore(); await sync(); await deviceNotice() }),
    logout: () => action('正在退出登录', async () => { remember(null); try { await client.logout() } finally { clearWork() } await sync(); setError(null); setNotice(null) }),
    newWork: () => { if (busyRef.current) return; remember(null); activeDraft.current = null; setDraft(null); setDetail(null); setHint(null); setError(null); setNotice(null) },
    /** A correction takes exactly one photo; homework takes one or more pages. */
    importPhotos: (files: File[], correction?: CorrectionTarget, curriculum?: CurriculumContext | null) => action(files.length > 1 ? `正在准备 ${files.length} 张照片` : '正在准备照片', async () => { const next = correction ? await client.createCorrection(correction.caseId, files[0], 'unknown', correction.parentAttemptId) : await client.createDraft(files, '学校作业', curriculum ?? null); show(next); await sync() }),
    setStudentGrade: (grade: number) => action('正在保存年级', async () => { const next = await client.setStudentGrade(grade); setStudentCurriculum(next); setNotice('年级已保存，课程范围按对应教材出版社更新。') }),
    retryQuestion: (row: CaseDetail) => action('正在重新检查这道题', async () => {
      if (!draft?.homeworkId) throw new Error('请先打开这份作业再重试。')
      const next = await client.runDraft(draft.id, { retryFailed: true, forceCheckCaseIds: [row.case.id], forceCheckToken: crypto.randomUUID() })
      setDraft(next); setDetail(next.detail); setRecordVersion(v => v + 1); await sync()
    }),
    setQuestionSubject: (row: CaseDetail, subject: string) => action('正在按所选学科重新判题', async () => {
      if (!draft?.homeworkId) throw new Error('当前作业无法重新判题，请刷新后重试。')
      await client.setQuestionSubject(row.question.question_id, row.question.revision, subject)
      const next = await client.runDraft(draft.id, { forceCheckCaseIds: [row.case.id], forceCheckToken: crypto.randomUUID() })
      setDraft(next); setDetail(next.detail); setRecordVersion(v => v + 1); await sync()
    }),
    addPages: (files: File[]) => action('正在加入照片', async () => { if (!draft) return; show(await client.addPages(draft.id, files)); await sync() }),
    removePage: (pageId: string) => action('正在删除这一页', async () => { if (!draft) return; const next = await client.removePage(draft.id, pageId); show(next); if (!next) setNotice('最后一页已删除，这份草稿也一起移除了。'); await sync() }),
    movePage: (pageId: string, delta: number) => action('正在调整页序', async () => { if (!draft) return; show(await client.movePage(draft.id, pageId, delta)); await sync() }),
    editPage: (pageId: string, change: EditChange) => action(change.crop ? '正在裁切照片' : change.reset ? '正在恢复原图' : '正在旋转照片', async () => { if (!draft) return; show(await client.editPage(draft.id, pageId, change)); await sync() }),
    replacePage: (pageId: string, file: File) => action('正在换上新照片', async () => { if (!draft) return; show(await client.replacePage(draft.id, pageId, file)); setNotice('这一页已换成新照片，可以继续上传。'); await sync() }),
    discardDraft: (id: string) => action('正在删除草稿', async () => { await client.discardDraft(id); if (activeDraft.current === id) show(null); setNotice('这份还没上传的草稿已从这台设备删除。'); await sync() }),
    openDraft: (id: string) => action('正在打开作业', () => selectDraft(id)),
    run: () => action('正在检查作业', async () => { if (!draft) return; const next = await client.runDraft(draft.id, { retryFailed: draft.stage === 'failed' }); setDraft(next); setDetail(next.detail); setRecordVersion(v => v + 1); await sync() }),
    rerecognize: () => action('正在重新识别', async () => { if (!draft) return; const next = await client.runDraft(draft.id, { rerecognize: true }); setDraft(next); setDetail(next.detail); setRecordVersion(v => v + 1); await sync() }),
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
      show(null); await sync()
      setNotice(`已从这台设备清除 ${report.drafts} 份草稿和 ${report.operations} 条提交记录${report.expiredPending ? `（其中 ${report.expiredPending} 条结果尚未确认）` : ''}。服务器上的作业不受影响。`)
    }),
  }
}
export type Workspace = ReturnType<typeof useStudentWorkspace>
