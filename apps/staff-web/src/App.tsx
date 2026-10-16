import { useEffect, useRef, useState } from 'react'
import { ArrowLeft, ArrowRight, BookOpen, CircleHelp, Eye, FileImage, Focus, Inbox, Link2, LoaderCircle, LockKeyhole, LogOut, Maximize2, MessageCircle, RefreshCw, Search, ShieldCheck, Sparkles, X } from 'lucide-react'
import { ApiError, errorMessage, StaffApi } from './api/client'
import type { HelpRequest, IdentityContext, SubmitReview } from './api/contracts.generated'
import { loadWorkspace, type CaseWorkspace } from './workspace'
import { sampleHelp, sampleIdentity, sampleWorkspace } from './sample'
import ReviewPanel from './components/ReviewPanel'

const kindLabels = { question: '作业答疑', recognition: '识别待核实', judgment: '判断待核实', appeal: '申请重查' }
const statusLabels = { waiting: '待接手', claimed: '处理中', resolved: '帮助已结束', unresolved: '仍需帮助', cancelled: '已取消' }

export default function App() {
  const [mode, setMode] = useState<'offline' | 'live' | 'sample'>('offline')
  const [api, setApi] = useState<StaffApi | null>(null)
  const [identity, setIdentity] = useState<IdentityContext | null>(null)
  const [queue, setQueue] = useState<HelpRequest[]>([])
  const [nextCursor, setNextCursor] = useState<string | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const [workspace, setWorkspace] = useState<CaseWorkspace | null>(null)
  const [search, setSearch] = useState('')
  const [filter, setFilter] = useState<'all' | 'waiting' | 'claimed'>('all')
  const [connecting, setConnecting] = useState(false)
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [writeUncertain, setWriteUncertain] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [showConnect, setShowConnect] = useState(false)
  const [baseUrl, setBaseUrl] = useState('')
  const [token, setToken] = useState('')
  const [showRegion, setShowRegion] = useState(true)
  const [zoom, setZoom] = useState(false)
  const [assetIndex, setAssetIndex] = useState(0)
  const [imageSize, setImageSize] = useState({ width: 0, height: 0 })
  const [imageFailed, setImageFailed] = useState(false)
  const connectionAbort = useRef<AbortController | null>(null)
  const detailAbort = useRef<AbortController | null>(null)
  const epoch = useRef(0)
  const sample = mode === 'sample'

  function disconnect() {
    epoch.current += 1
    connectionAbort.current?.abort(); detailAbort.current?.abort(); api?.disconnect()
    setApi(null); setIdentity(null); setQueue([]); setNextCursor(null); setSelected(null); setWorkspace(null)
    setMode('offline'); setToken(''); setError(null); setNotice(null); setLoading(false); setConnecting(false); setBusy(false); setWriteUncertain(false)
  }
  function fail(cause: unknown) {
    if (cause instanceof ApiError && cause.status === 401) disconnect()
    if (cause instanceof ApiError && cause.status === 403) { setQueue([]); setSelected(null); setWorkspace(null) }
    setError(errorMessage(cause))
  }
  function startSample() {
    disconnect(); setShowConnect(false); setMode('sample'); setIdentity(sampleIdentity); setQueue([sampleHelp]); setSelected(sampleHelp.id); setWorkspace(sampleWorkspace)
  }
  async function connect(event: React.FormEvent) {
    event.preventDefault(); setError(null); setConnecting(true)
    const controller = new AbortController(); connectionAbort.current?.abort(); connectionAbort.current = controller
    const activeEpoch = ++epoch.current
    let client: StaffApi | undefined
    try {
      client = new StaffApi(baseUrl, token)
      const context = await client.me(controller.signal)
      if (!context.roles.some(role => ['sitting_teacher', 'campus_admin'].includes(role))) throw new ApiError(403, 'Staff access required')
      if (activeEpoch !== epoch.current) return
      api?.disconnect(); setApi(client); setIdentity(context); setMode('live'); setQueue([]); setSelected(null); setWorkspace(null); setShowConnect(false); setToken('')
      const page = await client.helpQueue(undefined, controller.signal)
      if (activeEpoch !== epoch.current) return
      setQueue(page.items); setNextCursor(page.next_cursor)
    } catch (cause) {
      if (!controller.signal.aborted && activeEpoch === epoch.current) {
        if (cause instanceof ApiError && cause.status === 401) { client?.disconnect(); disconnect() }
        if (cause instanceof ApiError && cause.status === 403) { client?.disconnect(); disconnect() }
        setError(cause instanceof ApiError ? errorMessage(cause) : cause instanceof Error ? cause.message : '无法建立连接')
      }
    }
    finally { if (activeEpoch === epoch.current) setConnecting(false) }
  }
  async function refresh(append = false) {
    if (!api || sample) return
    setLoading(true); setError(null); setNotice(null)
    const activeEpoch = epoch.current
    try {
      const page = await api.helpQueue(append ? nextCursor ?? undefined : undefined)
      if (activeEpoch !== epoch.current) return
      setQueue(old => append ? [...old, ...page.items.filter(item => !old.some(previous => previous.id === item.id))] : page.items)
      setNextCursor(page.next_cursor)
      if (!append) { setSelected(null); setWorkspace(null) }
    } catch (cause) { if (activeEpoch === epoch.current) fail(cause) }
    finally { if (activeEpoch === epoch.current) setLoading(false) }
  }

  useEffect(() => {
    if (sample) { setWorkspace(selected ? sampleWorkspace : null); return }
    const help = queue.find(item => item.id === selected)
    if (!api || !help) { setWorkspace(null); return }
    const controller = new AbortController(); detailAbort.current?.abort(); detailAbort.current = controller
    setWorkspace(null); setLoading(true); setError(null); setAssetIndex(0); setImageFailed(false); setImageSize({ width: 0, height: 0 })
    loadWorkspace(api, help, controller.signal)
      .then(data => { if (!controller.signal.aborted) setWorkspace(data) })
      .catch(cause => { if (!controller.signal.aborted) fail(cause) })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [api, selected, queue, sample])
  useEffect(() => () => { connectionAbort.current?.abort(); detailAbort.current?.abort() }, [])

  async function mutate(operation: () => Promise<unknown>, retryOriginal = false) {
    if (!api || sample || (writeUncertain && !retryOriginal)) return
    setBusy(true); setError(null); setNotice(null)
    const activeEpoch = epoch.current
    try {
      await operation()
      if (activeEpoch !== epoch.current) return
      setWriteUncertain(false)
      setNotice('服务端已确认本次操作。正在读取最新记录。')
      const page = await api.helpQueue()
      if (activeEpoch !== epoch.current) return
      setQueue(page.items); setNextCursor(page.next_cursor)
    } catch (cause) { if (activeEpoch === epoch.current) { setWriteUncertain(api.hasPendingWrite()); fail(cause) } }
    finally { if (activeEpoch === epoch.current) setBusy(false) }
  }
  const visibleQueue = queue.filter(item => (filter === 'all' || item.status === filter) && (item.note.includes(search) || item.id.includes(search)))
  const student = identity?.authorized_students.find(item => item.student_id === workspace?.correction.scope.student_id)
  const asset = workspace?.assets[assetIndex]
  const imageUrl = asset && sample ? '/sample-homework.svg' : asset && new Date(asset.expires_at).getTime() > Date.now() && /^https:\/\//.test(asset.read_url) ? asset.read_url : null
  const regions = workspace?.question.record.regions.filter(region => region.asset_id === asset?.asset_id) ?? []

  return <div className="app-shell">
    <header className="app-header">
      <a className="brand" href="#main"><span className="brand-mark"><BookOpen size={23} strokeWidth={1.6} /></span><span>学伴<span className="brand-divider">/</span><strong>坐班工作台</strong></span></a>
      <div className="header-actions"><span className={`connection-state ${mode === 'live' ? 'connected' : ''}`}><i />{sample ? '合成样例' : mode === 'live' ? '身份已授权' : '尚未连接'}</span>
        {mode === 'offline' ? <><button className="button text-button" onClick={startSample}><Eye size={15} />合成样例预览</button><button className="button primary" onClick={() => setShowConnect(true)}><Link2 size={15} />连接工作台</button></> : <button className="button" onClick={disconnect}><LogOut size={15} />{sample ? '退出预览' : '断开并清除会话'}</button>}
      </div>
    </header>
    {sample && <div className="sample-banner"><Sparkles size={16} /><strong>合成样例预览</strong><span>无真实学生数据 · 不调用后台 · 所有处理操作均不保存</span><button onClick={disconnect} aria-label="退出合成预览"><X size={16} /></button></div>}
    <div className="workspace-heading"><div><p className="eyebrow">让孩子继续自己的作业</p><h1>每一次卡住，都有人接住。</h1></div><div className="workspace-subtitle"><ShieldCheck size={17} /><span>对照原图，有依据地帮助订正</span></div></div>
    {error && <div className="status-message error" role="alert"><CircleHelp size={17} /><span>{error}</span><button onClick={() => setError(null)} aria-label="关闭错误提示"><X size={16} /></button></div>}
    {notice && <div className="status-message success" role="status"><ShieldCheck size={17} /><span>{notice}</span></div>}
    {writeUncertain && <div className="status-message error" role="status"><CircleHelp size={17} /><span>上一笔提交结果尚未确认。表单暂时锁定；重试会沿用原内容与幂等键。刷新队列不会解除这一状态，请勿刷新浏览器或重复创建操作。</span><button className="button" disabled={busy} onClick={() => mutate(() => api!.retryPendingWrite(), true)}>重试原操作</button></div>}

    <main id="main" className="workbench">
      <aside className="queue-panel" aria-label="待办与求助队列">
        <div className="queue-heading"><div><h2>待办与求助</h2><p>{sample ? '1 条合成记录' : mode === 'live' ? '当前已加载的授权队列' : '连接后读取授权范围'}</p></div><button className="icon-button" aria-label="刷新队列" disabled={mode !== 'live' || loading || busy} onClick={() => refresh()}><RefreshCw size={17} className={loading ? 'spin' : ''} /></button></div>
        <label className="search-box"><Search size={16} /><input aria-label="搜索求助内容或编号" placeholder="搜索求助内容" value={search} onChange={event => setSearch(event.target.value)} /></label>
        <div className="queue-tabs" aria-label="筛选求助状态">{([['all', '全部'], ['waiting', '待接手'], ['claimed', '处理中']] as const).map(([value, label]) => <button key={value} aria-pressed={filter === value} className={filter === value ? 'active' : ''} onClick={() => setFilter(value)}>{label}</button>)}</div>
        <div className="queue-list">{visibleQueue.map(help => <button key={help.id} className={`queue-item ${selected === help.id ? 'selected' : ''}`} onClick={() => { setSelected(help.id); setNotice(null); setAssetIndex(0); setImageFailed(false) }} disabled={busy}>
          <div className="queue-item-top"><span className="queue-avatar"><MessageCircle size={18} /></span><span className="tag">{kindLabels[help.kind]}</span><span className="queue-status">{statusLabels[help.status]}</span></div>
          <h3>{sample ? '合成学生 A · 数学作业' : '作业中的求助'}</h3><p>{help.note}</p><footer><span>{sample ? '样例时间 16:32' : new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }).format(new Date(help.created_at))}</span><ArrowRight size={15} /></footer>
        </button>)}
          {visibleQueue.length === 0 && <div className="queue-empty"><Inbox size={30} strokeWidth={1.2} /><p>{mode === 'offline' ? '队列尚未连接' : loading ? '正在读取队列…' : error ? '暂时无法读取队列' : '当前筛选下没有记录'}</p><span>{mode === 'offline' ? '授权后，孩子的求助会显示在这里。' : '未加载的记录不会计作已处理。'}</span></div>}
          {nextCursor && <button className="button full" disabled={loading || busy} onClick={() => refresh(true)}>加载更多</button>}
        </div>
        <div className="queue-footer"><CircleHelp size={16} /><p>先帮助当前卡住的孩子。<br />不额外安排课程或练习。</p></div>
      </aside>

      <section className={`document-panel ${zoom ? 'expanded-document' : ''}`} aria-label="作业原图与题区">
        {workspace ? <>
          <div className="document-heading"><div><div className="breadcrumb">{sample ? '合成作业' : student?.display_name ?? '已授权作业'}<span>/</span>当前求助</div><h2>{workspace.question.record.stem_text ?? '题目文字待核实'}</h2></div><button className="button" disabled={sample || busy || workspace.help.status !== 'waiting'} onClick={() => mutate(() => api!.claim(workspace.help))}><ShieldCheck size={15} />{workspace.help.status === 'claimed' ? '已接手' : '接手处理'}</button></div>
          <div className="help-quote"><MessageCircle size={17} /><div><small>孩子说</small><p>“{workspace.help.note}”</p></div></div>
          <div className="image-toolbar"><div><span className="view-label"><FileImage size={15} />原始照片</span><span className="page-indicator">{assetIndex + 1} / {workspace.assets.length}</span></div><div><button className={`icon-button ${showRegion ? 'is-on' : ''}`} title="显示或隐藏题区" aria-label="显示或隐藏题区" aria-pressed={showRegion} onClick={() => setShowRegion(!showRegion)}><Focus size={17} /></button><button className="icon-button" title="展开原图" aria-label={zoom ? '收起原图' : '展开原图'} onClick={() => setZoom(!zoom)}>{zoom ? <X size={17} /> : <Maximize2 size={16} />}</button></div></div>
          <div className="paper-stage"><div className="paper-wrap">
            {imageUrl && !imageFailed ? <><img src={imageUrl} alt={sample ? '合成数学作业样张，非真实学生数据' : '当前作业原图'} referrerPolicy="no-referrer" onLoad={event => setImageSize({ width: event.currentTarget.naturalWidth, height: event.currentTarget.naturalHeight })} onError={() => setImageFailed(true)} />
              {showRegion && imageSize.width > 0 && <svg className="region-overlay" viewBox={`0 0 ${imageSize.width} ${imageSize.height}`} aria-label="当前题目范围">{regions.map((region, i) => <rect key={i} x={region.x} y={region.y} width={region.width} height={region.height} />)}</svg>}
            </> : <div className="photo-error"><FileImage size={34} /><p>原图暂时不可读取</p><small>读取地址可能过期或尚未接通。请刷新当前队列后重新进入；不会用其他图片替代。</small></div>}
          </div></div>
          <div className="document-footer"><span><span className="region-legend" />当前题区 · 原图像素定位</span>{workspace.assets.length > 1 && <div className="button-row"><button className="icon-button" disabled={assetIndex === 0} aria-label="上一张原图" onClick={() => { setAssetIndex(assetIndex - 1); setImageFailed(false); setImageSize({ width: 0, height: 0 }) }}><ArrowLeft size={16} /></button><button className="icon-button" disabled={assetIndex === workspace.assets.length - 1} aria-label="下一张原图" onClick={() => { setAssetIndex(assetIndex + 1); setImageFailed(false); setImageSize({ width: 0, height: 0 }) }}><ArrowRight size={16} /></button></div>}</div>
        </> : <div className="desk-empty"><div className="empty-illustration"><span className="paper-line"/><span className="paper-line"/><span className="paper-line short"/><span className="empty-stamp"><LockKeyhole size={24} /></span></div><p className="eyebrow">原图、作答与订正，在同一张桌面</p><h2>{loading ? '正在读取这次求助' : mode === 'offline' ? '先连接，再开始陪伴。' : '从左侧选择一条求助'}</h2><p>{loading ? '正在核对原图访问权限和有效版本。' : '这里会展示孩子的作业原图。先看清发生了什么，再给恰到好处的帮助。'}</p>{mode === 'offline' && <div className="button-row"><button className="button primary" onClick={() => setShowConnect(true)}>连接工作台<ArrowRight size={16} /></button><button className="button" onClick={startSample}>看看合成样例</button></div>}{loading && <LoaderCircle className="spin" size={24} />}<div className="empty-steps"><span>01 对照原图</span><span>02 帮助订正</span><span>03 留下依据</span></div></div>}
      </section>
      {workspace ? <ReviewPanel key={`${workspace.help.id}-${workspace.correction.revision}-${workspace.help.revision}`} workspace={workspace} identity={identity} sample={sample} busy={busy || writeUncertain} onReview={(payload: SubmitReview) => mutate(() => api!.review(workspace.correction.id, payload))} onComplete={(outcome, note) => mutate(() => api!.complete(workspace.help, outcome, note))} /> : <aside className="review-panel review-empty"><span className="round-icon"><ShieldCheck size={23} /></span><h2>每个结论，都有来处。</h2><p>选择求助后，在这里查看候选判断、孩子已经看过的提示和每次订正。</p><div className="principle"><span>01</span><p>AI 初判与老师确认<br />清楚区分</p></div><div className="principle"><span>02</span><p>帮助结束与题目答对<br />分别记录</p></div><div className="principle"><span>03</span><p>保留原图和修改过程<br />不覆盖历史</p></div><small>当前求助、复核后端仍待接通。</small></aside>}
    </main>
    <footer className="app-footer"><span>学伴 · 坐班支持</span><span>{sample ? '预览中的题目与人物均为合成' : '访问凭据仅保留在本次页面会话中'}</span></footer>

    {showConnect && <div className="modal-backdrop" onClick={() => { if (!connecting) setShowConnect(false) }}><section className="connection-dialog" role="dialog" aria-modal="true" aria-labelledby="connect-title" onClick={event => event.stopPropagation()}><button className="icon-button modal-close" aria-label="关闭连接窗口" disabled={connecting} onClick={() => setShowConnect(false)}><X size={20} /></button><span className="round-icon"><LockKeyhole size={24} /></span><h2 id="connect-title">连接坐班工作台</h2><p>使用后端签发的真实授权。不会创建演示账号，也不会保存访问令牌。</p><form onSubmit={connect}><label>服务地址<input type="url" required placeholder="https://api.example.com" value={baseUrl} onChange={event => setBaseUrl(event.target.value)} autoComplete="url" disabled={connecting} /></label><label>本次访问令牌<input type="password" required placeholder="Bearer 访问令牌，不含 Bearer 前缀" value={token} onChange={event => setToken(event.target.value)} autoComplete="off" disabled={connecting} /></label><div className="preview-note">当前版本首先核验身份；求助与复核接口未接通时会显示实际错误。</div><button className="button primary full" disabled={connecting}>{connecting ? <LoaderCircle size={16} className="spin" /> : <Link2 size={16} />}{connecting ? '正在核验授权…' : '核验并连接'}</button></form><button className="button text-button full" disabled={connecting} onClick={startSample}><Eye size={16} />只看合成样例，不连接服务</button></section></div>}
  </div>
}
