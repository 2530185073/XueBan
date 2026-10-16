import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { BookOpen, CircleHelp, Eye, LoaderCircle, LockKeyhole, LogOut, ShieldCheck, Sparkles, X } from 'lucide-react'
import { ApiError, errorMessage, isConflict, StaffApi } from './api/client'
import type { Accepted, HelpRequest, IdentityContext, ImageAsset, QuestionRead, RepairLayout, ReviseAnswer, ReviseQuestion, SubmitReview } from './api/contracts.generated'
import type { ReviewQueueItem } from './api/types'
import { isTerminal, pollJob } from './jobs'
import { applyPage, emptyQueue, helpId, reviewId, type QueueState, type QueueTab, type Selection } from './queue'
import { sampleCaseId, sampleHelp, sampleIdentity, sampleImage, sampleQuestions, sampleReviewItem, sampleWorkspace } from './sample'
import { currentAttempt, editBlock, focusHelp, loadHomeworkQuestions, loadWorkspace, newCaseIds, type CaseWorkspace } from './workspace'
import CaseDesk, { type RepairState } from './components/CaseDesk'
import { LoginScreen, PasswordScreen } from './components/LoginScreen'
import QueuePanel from './components/QueuePanel'
import ReviewPanel from './components/ReviewPanel'

type Phase = 'login' | 'password' | 'ready' | 'sample'
const STAFF_ROLES = ['sitting_teacher', 'campus_admin']
// setTimeout delays above 2^31-1 ms overflow and fire immediately.
const MAX_TIMER = 2_147_000_000

export default function App() {
  const api = useMemo(() => new StaffApi(window.location.origin, { proxyLoopbackAssets: import.meta.env.DEV }), [])
  const [phase, setPhase] = useState<Phase>('login')
  const [username, setUsername] = useState('')
  const [expiresAt, setExpiresAt] = useState<number | null>(null)
  const [identity, setIdentity] = useState<IdentityContext | null>(null)
  const [tab, setTab] = useState<QueueTab>('help')
  const [help, setHelp] = useState<QueueState<HelpRequest>>(emptyQueue)
  const [review, setReview] = useState<QueueState<ReviewQueueItem>>(emptyQueue)
  const [names, setNames] = useState<Map<string, string>>(new Map())
  const [selection, setSelection] = useState<Selection | null>(null)
  const [ws, setWs] = useState<CaseWorkspace | null>(null)
  const [attemptId, setAttemptId] = useState('')
  const [queueLoading, setQueueLoading] = useState(false)
  const [wsLoading, setWsLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [writeUncertain, setWriteUncertain] = useState(false)
  const [repair, setRepair] = useState<RepairState | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const epoch = useRef(0)
  const images = useRef(new Map<string, string>())
  const imageHomework = useRef<string | null>(null)
  const wsAbort = useRef<AbortController | null>(null)
  const pollAbort = useRef<AbortController | null>(null)
  const checkAbort = useRef<AbortController | null>(null)
  const selectionRef = useRef<Selection | null>(null)
  const sample = phase === 'sample'
  const ready = phase === 'ready'

  const dropImages = useCallback(() => { for (const url of images.current.values()) URL.revokeObjectURL(url); images.current.clear(); imageHomework.current = null }, [])

  const reset = useCallback((next: Phase, message: string | null = null) => {
    epoch.current += 1
    wsAbort.current?.abort(); pollAbort.current?.abort(); checkAbort.current?.abort(); dropImages()
    selectionRef.current = null
    setPhase(next); setExpiresAt(null); setIdentity(null); setHelp(emptyQueue()); setReview(emptyQueue()); setNames(new Map())
    setSelection(null); setWs(null); setAttemptId(''); setQueueLoading(false); setWsLoading(false); setBusy(false); setWriteUncertain(false); setRepair(null)
    setNotice(null); setError(message)
  }, [dropImages])

  useEffect(() => {
    api.onSessionEnded = reason => reset('login', reason === 'expired' ? '登录已到期，请重新登录。本页的学生资料已清除。' : '登录已失效或已在别处撤销，请重新登录。本页的学生资料已清除。')
    return () => { api.onSessionEnded = null }
  }, [api, reset])
  useEffect(() => {
    if (expiresAt === null) return
    const timer = setTimeout(() => { api.clear(); reset('login', '登录已到期，请重新登录。本页的学生资料已清除。') }, Math.min(MAX_TIMER, Math.max(0, expiresAt - Date.now())))
    return () => clearTimeout(timer)
  }, [api, expiresAt, reset])
  useEffect(() => () => { wsAbort.current?.abort(); pollAbort.current?.abort(); checkAbort.current?.abort(); dropImages() }, [dropImages])

  function fail(cause: unknown) {
    if (cause instanceof DOMException && cause.name === 'AbortError') return
    if (cause instanceof ApiError && cause.code === 'password_change_required') { setPhase('password'); setIdentity(null) }
    // 401 already reset the session through onSessionEnded.
    if (cause instanceof ApiError && cause.status === 401 && phase !== 'login' && !api.authenticated) return
    setError(errorMessage(cause))
  }

  const enrichNames = useCallback(async (helps: HelpRequest[], known: Set<string>, people: IdentityContext, generation: number) => {
    const missing = [...new Set(helps.map(item => item.case_id))].filter(id => !known.has(id))
    for (let i = 0; i < missing.length; i += 4) {
      const found = await Promise.all(missing.slice(i, i + 4).map(id => api.case(id).then(c => [id, people.authorized_students.find(s => s.student_id === c.scope.student_id)?.display_name] as const).catch(() => [id, undefined] as const)))
      if (generation !== epoch.current) return
      setNames(old => { const next = new Map(old); for (const [id, name] of found) if (name) next.set(id, name); return next })
    }
  }, [api])

  const loadQueues = useCallback(async (people: IdentityContext | null, more?: QueueTab) => {
    if (!people) return
    const generation = epoch.current
    setQueueLoading(true)
    try {
      const [helpPage, reviewPage] = await Promise.allSettled([
        more === 'review' ? Promise.resolve(null) : api.helpQueue(more === 'help' ? help.cursor ?? undefined : undefined),
        more === 'help' ? Promise.resolve(null) : api.reviewQueue(more === 'review' ? review.cursor ?? undefined : undefined),
      ])
      if (generation !== epoch.current) return
      const known = new Set(names.keys())
      if (reviewPage.status === 'fulfilled' && reviewPage.value) {
        const page = reviewPage.value
        setReview(old => applyPage(old, page, reviewId, more === 'review'))
        for (const item of page.items) known.add(item.case_id)
        setNames(old => new Map([...old, ...page.items.map(item => [item.case_id, item.student_name] as const)]))
      }
      if (helpPage.status === 'fulfilled' && helpPage.value) {
        const page = helpPage.value
        setHelp(old => applyPage(old, page, helpId, more === 'help'))
        void enrichNames(page.items, known, people, generation)
      }
      const rejected = [helpPage, reviewPage].find(result => result.status === 'rejected')
      if (rejected) fail((rejected as PromiseRejectedResult).reason)
    } finally { if (generation === epoch.current) setQueueLoading(false) }
  }, [api, help.cursor, review.cursor, names, enrichNames])

  const loadCase = useCallback(async (caseId: string, keepView: boolean) => {
    if (phase === 'sample') { setWs(sampleWorkspace); if (!keepView) setAttemptId(sampleWorkspace.correction.current_attempt_id); return }
    wsAbort.current?.abort()
    const controller = new AbortController(); wsAbort.current = controller
    const generation = epoch.current
    if (!keepView) setWs(null)
    setWsLoading(true)
    try {
      const data = await loadWorkspace(api, caseId, controller.signal)
      if (controller.signal.aborted || generation !== epoch.current || selectionRef.current?.caseId !== caseId) return
      if (imageHomework.current !== data.homework.id) { dropImages(); imageHomework.current = data.homework.id }
      setWs(data)
      setAttemptId(old => keepView && data.attempts.some(a => a.record.id === old) ? old : data.correction.current_attempt_id)
    } catch (cause) { if (!controller.signal.aborted && generation === epoch.current) fail(cause) }
    finally { if (!controller.signal.aborted && generation === epoch.current) setWsLoading(false) }
  }, [api, phase, dropImages])

  function select(next: Selection) {
    selectionRef.current = next
    setSelection(next); setNotice(null); setError(null)
    void loadCase(next.caseId, false)
  }

  async function refreshAll() {
    await Promise.all([loadQueues(identity), selectionRef.current ? loadCase(selectionRef.current.caseId, true) : Promise.resolve()])
  }

  async function enter() {
    const people = await api.me()
    if (!people.roles.some(role => STAFF_ROLES.includes(role))) {
      await api.logout()
      reset('login', '此账号不是坐班老师或校区管理员账号，不能进入工作台。')
      return
    }
    setIdentity(people); setPhase('ready'); setError(null)
    await loadQueues(people)
  }

  async function login(name: string, password: string) {
    setBusy(true); setError(null); setNotice(null)
    try {
      const session = await api.login(name, password)
      setUsername(name.trim().toLowerCase()); setExpiresAt(Date.parse(session.expires_at))
      if (session.must_change_password) setPhase('password')
      else await enter()
      return true
    } catch (cause) { fail(cause); return false }
    finally { setBusy(false) }
  }

  async function changePassword(current: string, next: string) {
    setBusy(true); setError(null)
    try {
      const session = await api.changePassword(current, next)
      setExpiresAt(Date.parse(session.expires_at))
      await enter()
      setNotice('新密码已保存。以后请用新密码登录。')
      return true
    } catch (cause) { fail(cause); return false }
    finally { setBusy(false) }
  }

  async function logout() {
    setBusy(true)
    const confirmed = await api.logout()
    reset('login', null)
    setNotice(confirmed ? '已退出登录，本页的学生资料已清除。' : null)
    if (!confirmed) setError('本页已退出，但服务器尚未确认会话撤销；该会话在到期前仍可能有效。')
  }

  function startSample() {
    reset('sample')
    setIdentity(sampleIdentity)
    setHelp({ items: [sampleHelp], cursor: null, loaded: true })
    setReview({ items: [sampleReviewItem], cursor: null, loaded: true })
    setNames(new Map([[sampleCaseId, '合成学生 A']]))
    const first = { caseId: sampleCaseId, helpId: sampleHelp.id }
    selectionRef.current = first; setSelection(first); setWs(sampleWorkspace); setAttemptId(sampleWorkspace.correction.current_attempt_id)
  }

  function trackRepair(accepted: Accepted, homeworkId: string, before: string[]) {
    pollAbort.current?.abort()
    const controller = new AbortController(); pollAbort.current = controller
    const generation = epoch.current
    setRepair({ job: accepted.job, homeworkId, createdCaseIds: null })
    pollJob(signal => api.job(accepted.job.id, signal), accepted.job, {
      signal: controller.signal,
      onUpdate: job => { if (generation === epoch.current) setRepair(old => old && old.job.id === job.id ? { ...old, job } : old) },
    }).then(async job => {
      if (generation !== epoch.current) return
      if (!isTerminal(job.status)) { setNotice('补题/拆合仍在处理中，请稍后刷新查看结果。'); return }
      const created = job.status === 'succeeded' ? newCaseIds(before, await api.homework(homeworkId)) : null
      if (generation !== epoch.current) return
      setRepair(old => old && old.job.id === job.id ? { ...old, job, createdCaseIds: created } : old)
      await refreshAll()
    }).catch(cause => { if (!controller.signal.aborted && generation === epoch.current) fail(cause) })
  }

  function watchCheck(accepted: Accepted) {
    checkAbort.current?.abort()
    const controller = new AbortController(); checkAbort.current = controller
    const generation = epoch.current
    pollJob(signal => api.job(accepted.job.id, signal), accepted.job, { signal: controller.signal }).then(async job => {
      if (generation !== epoch.current) return
      if (job.status === 'succeeded') setNotice('重新检查已完成，已读取新的 AI 候选判断。')
      else if (isTerminal(job.status)) setError('重新检查未能完成，可以稍后再试，或直接人工初次核实。')
      await refreshAll()
    }).catch(cause => { if (!controller.signal.aborted && generation === epoch.current) fail(cause) })
  }

  const repairBefore = useRef<{ homeworkId: string; caseIds: string[] } | null>(null)

  async function mutate<T>(operation: () => Promise<T>, success: string, after?: (value: T) => void, retryOriginal = false): Promise<boolean> {
    if (!ready || busy || (writeUncertain && !retryOriginal)) return false
    setBusy(true); setError(null); setNotice(null)
    const generation = epoch.current
    try {
      const value = await operation()
      if (generation !== epoch.current) return false
      setWriteUncertain(false); setNotice(success)
      after?.(value)
      await refreshAll()
      return true
    } catch (cause) {
      if (generation !== epoch.current) return false
      setWriteUncertain(api.hasPendingWrite())
      if (isConflict(cause)) {
        setError(errorMessage(cause))
        await refreshAll()
      } else fail(cause)
      return false
    } finally { if (generation === epoch.current) setBusy(false) }
  }

  const retry = () => mutate(() => api.retryPendingWrite(), '服务端已确认原操作。已读取最新记录。', ({ path, value }) => {
    if (path.endsWith('/layout-revisions') && repairBefore.current) trackRepair(value as Accepted, repairBefore.current.homeworkId, repairBefore.current.caseIds)
  }, true)

  const loadImage = useCallback(async (asset: ImageAsset, signal: AbortSignal) => {
    if (phase === 'sample') return `${import.meta.env.BASE_URL}${sampleImage}`
    const cached = images.current.get(asset.id)
    if (cached) return cached
    const access = await api.assetAccess(asset.id, signal)
    const blob = await api.image(access.read_url, signal)
    const url = URL.createObjectURL(blob)
    if (signal.aborted) { URL.revokeObjectURL(url); throw signal.reason }
    images.current.set(asset.id, url)
    return url
  }, [api, phase])

  const homework = ws?.homework
  const loadQuestions = useCallback((): Promise<QuestionRead[]> => {
    if (phase === 'sample') return Promise.resolve(sampleQuestions)
    return homework ? loadHomeworkQuestions(api, homework) : Promise.resolve([])
  }, [api, phase, homework])

  const focus = ws ? focusHelp(ws, selection?.helpId ?? null) : null
  const block = ws ? editBlock(ws, focus, identity?.actor_id, sample) : null
  const studentName = ws ? identity?.authorized_students.find(s => s.student_id === ws.correction.scope.student_id)?.display_name ?? names.get(ws.correction.id) ?? '已授权学生' : ''
  const signedIn = ready || sample

  return <div className="app-shell">
    <header className="app-header">
      <a className="brand" href="#main"><span className="brand-mark"><BookOpen size={23} strokeWidth={1.6} /></span><span>学伴<span className="brand-divider">/</span><strong>坐班工作台</strong></span></a>
      <div className="header-actions">
        <span className={`connection-state ${ready ? 'connected' : ''}`}><i />{sample ? '合成样例' : ready ? `已登录 · ${username}` : phase === 'password' ? '待设置新密码' : '尚未登录'}</span>
        {ready && <button className="button" disabled={busy} onClick={() => void logout()}><LogOut size={15} />退出登录</button>}
        {sample && <button className="button" onClick={() => reset('login')}><LogOut size={15} />退出预览</button>}
        {phase === 'login' && <button className="button text-button" onClick={startSample}><Eye size={15} />合成样例预览</button>}
      </div>
    </header>
    {sample && <div className="sample-banner"><Sparkles size={16} /><strong>合成样例预览</strong><span>人物、题目与原图均为合成 · 不调用后台 · 所有操作都不会保存</span><button onClick={() => reset('login')} aria-label="退出合成预览"><X size={16} /></button></div>}

    {error && <div className="status-message error" role="alert"><CircleHelp size={17} /><span>{error}</span><button onClick={() => setError(null)} aria-label="关闭错误提示"><X size={16} /></button></div>}
    {notice && <div className="status-message success" role="status"><ShieldCheck size={17} /><span>{notice}</span><button onClick={() => setNotice(null)} aria-label="关闭提示"><X size={16} /></button></div>}
    {writeUncertain && <div className="status-message error" role="status"><CircleHelp size={17} /><span>上一笔提交结果尚未确认。表单暂时锁定；重试会沿用原内容与幂等键。请勿刷新浏览器（刷新会丢失本页的待确认记录）。</span><button className="button" disabled={busy} onClick={() => void retry()}>重试原操作</button></div>}

    {!signedIn ? <main id="main" className="auth-stage">
      <div className="auth-intro"><p className="eyebrow">让孩子继续自己的作业</p><h1>每一次卡住，<br />都有人接住。</h1><p>对照原图核实 AI 判断、接手孩子的求助、修订识别与切题。所有结论都记录依据，并与 AI 候选分开保存。</p></div>
      {phase === 'password' ? <PasswordScreen busy={busy} username={username} onChange={changePassword} onLogout={() => void logout()} /> : <LoginScreen busy={busy} onLogin={login} onSample={startSample} />}
    </main> : <main id="main" className="workbench">
      <QueuePanel tab={tab} onTab={setTab} help={help} review={review} names={names} selection={selection} onSelect={select} loading={queueLoading} busy={busy} sample={sample}
        onRefresh={() => { setError(null); void refreshAll() }} onMore={more => void loadQueues(identity, more)} />
      {ws ? <>
        <CaseDesk key={ws.correction.id} ws={ws} studentName={studentName} focus={focus} block={block} busy={busy || writeUncertain} sample={sample} attemptId={attemptId || ws.correction.current_attempt_id}
          loadImage={loadImage} loadQuestions={loadQuestions} repair={repair} onOpenCase={caseId => select({ caseId, helpId: null })}
          onReviseQuestion={(id: string, payload: ReviseQuestion) => mutate(() => api.reviseQuestion(id, payload), '题目已修订。旧判断已失效，请重新检查或人工核实。')}
          onReviseAnswer={(id: string, payload: ReviseAnswer) => mutate(() => api.reviseAnswer(id, payload), '答案识别已修订。旧判断已失效，请重新检查或人工核实。')}
          onRepair={(payload: RepairLayout, before: string[]) => { repairBefore.current = { homeworkId: ws.homework.id, caseIds: before }; return mutate(() => api.repairLayout(ws.homework.id, payload), '补题/拆合已受理，正在生成新题。', accepted => trackRepair(accepted, ws.homework.id, before)) }} />
        <ReviewPanel key={`${ws.correction.id}-review`} ws={ws} focus={focus} actorId={identity?.actor_id} block={block} sample={sample} busy={busy || writeUncertain}
          attemptId={attemptId || ws.correction.current_attempt_id} onAttempt={setAttemptId}
          onClaim={target => mutate(() => api.claim(target), '已接手这条求助。')}
          onComplete={(target, outcome, note) => mutate(() => api.complete(target, outcome, note), outcome === 'resolved' ? '已记录：帮助已结束。题目对错仍以复核记录为准。' : '已记录：仍需帮助。')}
          onReview={(payload: SubmitReview) => mutate(() => api.submitReview(ws.correction.id, payload), '复核记录已保存。')}
          onStartCheck={() => { const attempt = currentAttempt(ws)!; return mutate(() => api.startCheck(ws.correction.id, { expected_revision: ws.correction.revision, attempt_id: attempt.record.id, question_revision: ws.question.current_revision, attempt_recognition_revision: attempt.record.recognition_revision }), '已请求重新检查，完成后会自动刷新这道题。', watchCheck) }} />
      </> : <>
        <section className="document-panel"><div className="desk-empty"><div className="empty-illustration"><span className="paper-line" /><span className="paper-line" /><span className="paper-line short" /><span className="empty-stamp"><LockKeyhole size={24} /></span></div>
          <p className="eyebrow">原图、作答与订正，在同一张桌面</p><h2>{wsLoading ? '正在读取这道题' : '从左侧选择一条记录'}</h2>
          <p>{wsLoading ? '正在核对原图访问权限和有效版本。' : '求助来自孩子；待核实来自 AI 候选与识别。先看清原图，再给恰到好处的帮助。'}</p>{wsLoading && <LoaderCircle className="spin" size={24} />}
          <div className="empty-steps"><span>01 对照原图</span><span>02 帮助订正</span><span>03 留下依据</span></div></div></section>
        <aside className="review-panel review-empty"><span className="round-icon"><ShieldCheck size={23} /></span><h2>每个结论，都有来处。</h2><p>选择记录后，在这里查看候选判断、孩子看过的提示和每一次订正。</p>
          <div className="principle"><span>01</span><p>AI 初判与老师确认<br />清楚区分</p></div><div className="principle"><span>02</span><p>帮助结束与题目答对<br />分别记录</p></div><div className="principle"><span>03</span><p>保留原图和修改过程<br />不覆盖历史</p></div></aside>
      </>}
    </main>}
    <footer className="app-footer"><span>学伴 · 坐班支持</span><span>{sample ? '预览中的题目与人物均为合成' : '登录凭据只保留在本页内存中，不写入浏览器存储'}</span></footer>
  </div>
}
