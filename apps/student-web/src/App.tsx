import { useEffect, useRef, useState } from 'react'
import { ArrowLeft, ArrowRight, BookOpen, Camera, Check, ChevronRight, Clock3, History, ImagePlus, KeyRound, Leaf, LoaderCircle, LogOut, Map as MapIcon, Plus, RefreshCw, SlidersHorizontal, WifiOff, X } from 'lucide-react'
import { useStudentWorkspace } from './useStudentWorkspace'
import { casePresentation, correctionTarget as toCorrectionTarget, dateLabel, preferredCase, replacementTarget, stageLabels, uploadState, type CorrectionTarget } from './presentation'
import { ACCEPTED_TYPES, LIMITS_TEXT, MAX_PAGES } from './core'
import { PhotoPanel } from './components/PhotoPanel'
import { DraftPages } from './components/DraftPages'
import { ResumeDrafts } from './components/ResumeDrafts'
import { QuestionDetail } from './components/QuestionDetail'
import { LoginForm, PasswordForm } from './components/AccountForms'
import { AdminConsole } from './admin/AdminConsole'
import { HistoryPage } from './components/HistoryPage'
import { ResultsSummary } from './components/ResultsSummary'
import { KnowledgeMapPage, useKnowledgeMap } from './components/KnowledgeMap'
import { PreferencesPage } from './components/Preferences'
import { OptionalActivityCard } from './components/OptionalActivities'
import { PracticeRunner } from './components/PracticeRunner'
import { hasKnowledgeMap } from './learning'
import type { PracticeSession } from './core/learning'

type Page = 'work' | 'history' | 'password' | 'knowledge' | 'me' | 'practice'
/** What the next picked photo is for: a new draft, more pages, or a replacement for one page. */
type PickMode = 'new' | 'add' | { replace: string }

export default function App() {
  const workspace = useStudentWorkspace()
  const [page, setPage] = useState<Page>('work')
  const knowledge = useKnowledgeMap(workspace)
  const [practice, setPractice] = useState<{ session: PracticeSession; notice?: string } | null>(null)
  // Learning views hold only in-memory data of the signed-in student; drop them when that changes.
  useEffect(() => { setPractice(null); setPage(old => old === 'knowledge' || old === 'me' || old === 'practice' ? 'work' : old) }, [workspace.studentId])
  const startPractice = (session: PracticeSession, notice?: string) => { setPractice({ session, notice }); setPage('practice') }
  const [selectedCaseId, setSelectedCaseId] = useState<string | null>(null)
  const [dragging, setDragging] = useState(false)
  const fileInput = useRef<HTMLInputElement>(null)
  const cameraInput = useRef<HTMLInputElement>(null)
  const correctionTarget = useRef<CorrectionTarget | undefined>(undefined)
  const isAdmin = workspace.identity?.roles.includes('campus_admin') ?? false
  const students = workspace.identity?.authorized_students ?? []
  const student = students.find(item => item.student_id === workspace.studentId)
  const { draft, detail } = workspace
  const rows = detail?.cases ?? []
  const selected = preferredCase(rows, selectedCaseId, draft?.correction?.caseId)
  const hasWork = draft != null || detail != null
  const pickMode = useRef<PickMode>('new')
  const choosePhoto = (camera = false, correction?: CorrectionTarget, mode: PickMode = 'new') => {
    correctionTarget.current = correction; pickMode.current = mode
    const input = (camera ? cameraInput : fileInput).current; if (!input) return
    input.multiple = !camera && !correction && typeof mode === 'string'; input.click()
  }
  const importFiles = (list?: FileList | null) => {
    const files = [...(list ?? [])], mode = pickMode.current, correction = correctionTarget.current
    pickMode.current = 'new'; correctionTarget.current = undefined
    if (!files.length || workspace.busy) return
    if (typeof mode === 'object') { void workspace.replacePage(mode.replace, files[0]); return }
    if (mode === 'add' && draft?.editable && !draft.correction) { void workspace.addPages(files); return }
    setSelectedCaseId(correction?.caseId ?? null); setPage('work'); void workspace.importPhotos(correction ? files.slice(0, 1) : files, correction)
  }
  // A camera that never opened (permission refused, no camera) shows up as a cancelled picker.
  useEffect(() => {
    const input = cameraInput.current; if (!input) return
    const cancelled = () => workspace.showNotice('没有拍到照片。如果相机没有打开，请在浏览器设置里允许本网站使用相机，或改用“选择作业照片”从相册里选。')
    input.addEventListener('cancel', cancelled); return () => input.removeEventListener('cancel', cancelled)
  })
  const newWork = () => { workspace.newWork(); setSelectedCaseId(null); setPage('work') }
  const pending = draft && ['creating', 'uploading', 'recognizing', 'checking', 'waiting'].includes(draft.stage)

  return <div className="app-shell">
    <header className="topbar"><a href="/learn/" className="brand" aria-label="学伴首页" onClick={event => { event.preventDefault(); if (workspace.identity) newWork() }}><span className="brand-symbol"><BookOpen size={23} strokeWidth={1.6} /></span><span className="brand-name">学伴<span>把每一步，想明白</span></span></a>{workspace.account ? <>{workspace.identity && <nav aria-label="主要导航">{isAdmin ? <button className={page !== 'password' ? 'active' : ''} onClick={() => setPage('work')}><BookOpen size={17} /> 学员账号</button> : <><button className={page === 'work' ? 'active' : ''} onClick={() => setPage('work')}><BookOpen size={17} /> 我的作业</button><button className={page === 'history' ? 'active' : ''} onClick={() => { setPage('history'); void workspace.loadHistory() }}><History size={17} /> 历史记录</button>{hasKnowledgeMap(knowledge.map) && <button className={page === 'knowledge' ? 'active' : ''} onClick={() => { setPage('knowledge'); knowledge.refresh() }}><MapIcon size={17} /> 知识地图</button>}</>}</nav>}<div className="account"><span className="avatar">{student?.display_name.slice(0, 1) || (isAdmin ? '管' : '学')}</span><span className="account-name">{student?.display_name || workspace.account.username}</span>{workspace.identity && !isAdmin && <button className={`icon-button ${page === 'me' ? 'active' : ''}`} onClick={() => setPage('me')} aria-label="我的偏好与目标" title="我的偏好与目标"><SlidersHorizontal size={17} /></button>}{!workspace.account.must_change_password && <button className="icon-button" disabled={!!workspace.busy} onClick={() => setPage('password')} aria-label="修改密码" title="修改密码"><KeyRound size={17} /></button>}<button className="icon-button" disabled={!!workspace.busy} onClick={() => void workspace.logout().then(() => setPage('work'))} aria-label="退出登录" title="退出登录"><LogOut size={17} /></button></div></> : <span className="topbar-note"><Leaf size={16} /> 陪伴自主学习</span>}</header>
    <main>
      {workspace.error && <div className="message error" role="alert"><span>{workspace.error}</span><button className="icon-button" aria-label="关闭提示" onClick={workspace.clearError}><X size={18} /></button></div>}
      {workspace.notice && <div className="message success" role="status"><Check size={18} /><span>{workspace.notice}</span></div>}
      {workspace.loading ? <div className="loading-page"><LoaderCircle className="spin" size={30} /><h1>正在打开你的作业本</h1><p>请稍等，正在确认登录信息。</p></div> : workspace.account && (workspace.account.must_change_password || page === 'password') ? <PasswordForm workspace={workspace} required={workspace.account.must_change_password} onDone={() => setPage('work')} onCancel={() => setPage('work')} /> : !workspace.identity ? <div className="welcome-layout"><section className="welcome-story"><span className="eyebrow">一页作业，一点进步</span><h1>你的作业，<br />一起<span className="serif-accent">看清楚。</span></h1><p className="welcome-copy">先自己完成，再拍下来检查。<br />遇到不确定的地方，学伴和坐班老师陪你想明白。</p><div className="paper-guide"><div className="paper-pin" /><span className="note-label">今天，也按自己的节奏来</span>{['完成自己的学校作业', '拍清题目和自己的作答', '看提示，试着订正一步'].map((line, index) => <div className="guide-line" key={line}><span>0{index + 1}</span><p>{line}</p>{index === 2 && <span className="hand-mark">慢慢来</span>}</div>)}<div className="paper-foot">每一次认真思考，都值得被记住。</div></div></section><LoginForm workspace={workspace} /></div> : isAdmin ? <AdminConsole client={workspace.client} /> : <>
        <input className="sr-only" ref={fileInput} type="file" accept={ACCEPTED_TYPES.join(',')} aria-label="选择作业照片" onChange={event => { importFiles(event.target.files); event.target.value = '' }} />
        <input className="sr-only" ref={cameraInput} type="file" accept={ACCEPTED_TYPES.join(',')} capture="environment" aria-label="拍摄作业照片" onChange={event => { importFiles(event.target.files); event.target.value = '' }} />
        {!workspace.online && <div className="message offline" role="status"><WifiOff size={18} /><span>现在没有网络。照片和进度都保存在这台设备上，联网后可以继续检查，不会重复提交。</span></div>}
        {page === 'knowledge' && knowledge.map ? <KnowledgeMapPage workspace={workspace} map={knowledge.map} onRefresh={knowledge.refresh} /> : page === 'me' ? <PreferencesPage workspace={workspace} onBack={() => setPage('work')} /> : page === 'practice' && practice ? <PracticeRunner key={practice.session.id} workspace={workspace} initial={practice.session} notice={practice.notice} onClose={() => { setPractice(null); setPage('work') }} /> : page === 'history' ? <HistoryPage workspace={workspace} onNewWork={newWork} onOpenDraft={id => { void workspace.openDraft(id); setSelectedCaseId(null); setPage('work') }} onOpen={(id, caseId) => { setSelectedCaseId(caseId ?? null); setPage('work'); void workspace.openHomework(id).then(ok => { if (ok) setSelectedCaseId(caseId ?? null) }) }} /> : !hasWork && workspace.busy === '正在打开历史' ? <div className="loading-page" role="status"><LoaderCircle className="spin" size={28} /><h1>正在打开这份作业</h1><p>正在读取逐题结果和订正记录。</p></div> : !hasWork ? <section className="start-page"><div className="page-title-row"><div><span className="eyebrow">准备好了，就从这一页开始</span><h1>{student?.display_name ? `${student.display_name}，` : ''}今天的作业，一起看看。</h1><p>先完成自己的作答，再让学伴陪你检查。</p></div><span className="quiet-note"><Leaf size={17} /> 进步，不用着急</span></div><div className={`upload-workspace ${dragging ? 'dragging' : ''}`} onDragOver={event => { event.preventDefault(); setDragging(true) }} onDragLeave={() => setDragging(false)} onDrop={event => { event.preventDefault(); setDragging(false); correctionTarget.current = undefined; pickMode.current = 'new'; importFiles(event.dataTransfer.files) }}><div className="upload-sheet"><span className="sheet-corner" /><div className="upload-illustration"><div className="line long" /><div className="line" /><div className="written-line" /><div className="line long" /><span><ImagePlus size={29} strokeWidth={1.5} /></span></div><h2>把写好的作业放在这里</h2><p>拖入照片，或从电脑、平板中选择；几页作业可以一次选好。</p><button className="button primary" disabled={!!workspace.busy} onClick={() => choosePhoto()}><Plus size={19} /> 选择作业照片</button><button className="text-button" disabled={!!workspace.busy} onClick={() => choosePhoto(true)}><Camera size={17} /> 用相机拍一张</button><small>支持 JPG、PNG · {LIMITS_TEXT}</small></div><aside className="capture-tips"><span className="eyebrow">拍照小贴士</span><h3>拍清楚，<br />才看得明白。</h3><ol><li><span>01</span><div><strong>题目和作答一起拍</strong><p>别漏掉条件、图形和解题过程。</p></div></li><li><span>02</span><div><strong>镜头放平，光线均匀</strong><p>避开反光、阴影和模糊的字迹。</p></div></li><li><span>03</span><div><strong>不确定，可以先留待核实</strong><p>AI 初判供参考；对照原图，必要时请老师帮忙。</p></div></li></ol><span className="tips-footer">你先认真写，学伴陪你检查。</span></aside></div><ResumeDrafts workspace={workspace} onOpen={id => { void workspace.openDraft(id); setSelectedCaseId(null) }} /></section> : <section className="active-work"><div className="work-toolbar"><button className="text-button" disabled={!!workspace.busy} onClick={newWork}><ArrowLeft size={16} /> 新的一页</button><span>{draft?.title || detail?.homework.title || '学校作业'}<span className="toolbar-separator">/</span>{draft ? dateLabel(draft.createdAt) : '历史作业'}</span><button className="text-button" disabled={!!workspace.busy || !detail} onClick={() => void workspace.refreshDetail()}><RefreshCw size={15} /> 刷新结果</button></div><div className="work-grid">{draft?.editable ? <DraftPages key={draft.id} workspace={workspace} draft={draft} onAdd={camera => choosePhoto(camera, undefined, 'add')} onReplace={pageId => choosePhoto(false, undefined, { replace: pageId })} /> : <PhotoPanel workspace={workspace} row={selected} draft={draft} detail={detail} onReplacePage={pageId => choosePhoto(false, undefined, { replace: pageId })} />}<div className="results-panel"><div className="panel-heading"><span><span className="eyebrow">一步一步，想明白</span><h2>{draft?.stage === 'local' ? '照片清楚，就开始吧' : '这页作业，我们一起看'}</h2></span><BookOpen size={23} strokeWidth={1.4} /></div>{draft?.stage === 'local' ? <div className="ready-card"><span className="ready-symbol"><Check size={24} /></span><h3>{draft.correction ? '订正照片已准备好' : draft.pages.length > 1 ? `${draft.pages.length} 页照片已准备好` : '照片已准备好'}</h3><p>{draft.correction ? '检查一下这道题的订正是否拍完整。准备好后，再开始检查。' : '看看每一页的题目和作答是否完整、页序对不对。照片现在只在这台设备上，开始检查后才会上传。'}</p><button className="button primary full" disabled={!!workspace.busy || !workspace.online} onClick={() => void workspace.run()}>{workspace.busy ? <LoaderCircle className="spin" size={18} /> : <ArrowRight size={18} />} {draft.correction ? '开始检查这张订正' : draft.pages.length > 1 ? `开始检查这 ${draft.pages.length} 页作业` : '开始检查这张作业'}</button>{!workspace.online && <p className="muted small">联网后才能开始检查。</p>}{draft.correction ? <button className="text-button" disabled={!!workspace.busy} onClick={() => choosePhoto(false, replacementTarget(draft))}>换一张照片</button> : draft.pages.length < MAX_PAGES && <button className="text-button" disabled={!!workspace.busy} onClick={() => choosePhoto(false, undefined, 'add')}><Plus size={15} /> 还有一页？再加一页</button>}</div> : <>
          {(pending || workspace.busy === '正在检查作业') && <div className="processing" role="status"><LoaderCircle className="spin" size={22} /><div><strong>{draft ? stageLabels[draft.stage] : '正在检查作业'}</strong><p>{draft && draft.pageCount > 1 && draft.uploadedCount < draft.pageCount ? `已上传 ${draft.uploadedCount} / ${draft.pageCount} 页。` : ''}{draft?.stage === 'recognizing' && !draft.correction ? '正在通读整页，核对题目和你的作答。' : ''}原图和进度会保留，不必重复提交。</p></div></div>}
          {draft?.error && <div className="inline-error"><strong>这次还没有完成</strong><p>{draft.error.message}</p>{draft.pageCount > 0 && <span className={`status-pill ${uploadState(draft).tone}`}>{uploadState(draft).label}</span>}</div>}
          {draft && !workspace.busy && ['failed', 'waiting', 'creating', 'uploading', 'recognizing', 'checking'].includes(draft.stage) && <button className="button secondary full" disabled={!workspace.online} onClick={() => void workspace.run()}><RefreshCw size={16} /> 继续未完成的检查</button>}
          {rows.length > 0 ? <><ResultsSummary rows={rows} detail={detail} /><div className="question-list" aria-label="这次作业的题目">{rows.map((row, index) => { const result = casePresentation(row); return <button key={row.case.id} className={`question-item ${selected?.case.id === row.case.id ? 'selected' : ''}`} aria-current={selected?.case.id === row.case.id ? 'true' : undefined} onClick={() => setSelectedCaseId(row.case.id)}><span className="question-index">{String(index + 1).padStart(2, '0')}</span><span className="question-title">{row.question.stem_text || '题干待核实'}<small>题目 {index + 1}</small></span><span className={`status-pill ${result.tone}`}>{result.label}</span><ChevronRight size={16} /></button> })}</div>{selected && <QuestionDetail key={`${selected.case.id}:${selected.attempt.id}`} row={selected} workspace={workspace} onCorrection={() => choosePhoto(false, toCorrectionTarget(selected))} onContinue={rows.length > 1 ? () => setSelectedCaseId(rows[(rows.findIndex(row => row.case.id === selected.case.id) + 1) % rows.length].case.id) : undefined} />}</> : !pending && <div className="result-empty"><ScanNote /><h3>还没有可查看的逐题结果</h3><p>{detail?.homework.coverage.note || '未读清的地方不会被当成正确。可以重新拍清楚，或请老师一起核实。'}</p><div className="hint-actions">{detail && !detail.cases.length && workspace.draft?.homeworkId && !workspace.draft.correction && <button className="button primary" disabled={!!workspace.busy} onClick={() => void workspace.rerecognize()}><RefreshCw size={17} /> 重新核对这张照片</button>}<button className="button secondary" disabled={!!workspace.busy} onClick={() => choosePhoto()}><Camera size={17} /> 再拍清楚一点</button></div></div>}
        </>}</div></div></section>}
        {page === 'work' && <OptionalActivityCard workspace={workspace} onStarted={startPractice} />}
      </>}
      {workspace.busy && <div className="activity-indicator" role="status"><LoaderCircle className="spin" size={16} /><span>{workspace.busy}</span></div>}
    </main><footer className="footer"><span>学伴 <span className="footer-dot">·</span> 陪你完成自己的作业</span><span>AI 初判供参考，拿不准时请老师一起看。</span></footer>
  </div>
}
function ScanNote() { return <span className="empty-symbol"><Clock3 size={29} strokeWidth={1.3} /></span> }
