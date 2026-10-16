import { useEffect, useMemo, useRef, useState } from 'react'
import { Check, Eye, EyeOff, KeyRound, LoaderCircle, Plus, RefreshCw, Search, ShieldCheck, Users, X } from 'lucide-react'
import { errorMessage } from './api'
import { AccountOperations } from './operations'
import type { AccountList, AdminClient, CredentialReceipt, PendingSummary, StudentAccount } from './types'
import './admin.css'

export function StudentAccounts({ client }: { client: AdminClient }) {
  const operations = useMemo(() => new AccountOperations(client), [client])
  const live = useRef(true)
  const working = useRef(false)
  const [list, setList] = useState<AccountList>({ items: [], campuses: [] })
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [search, setSearch] = useState('')
  const [studentName, setStudentName] = useState('')
  const [username, setUsername] = useState('')
  const [campusId, setCampusId] = useState('')
  const [password, setPassword] = useState('')
  const [resetPassword, setResetPassword] = useState('')
  const [selected, setSelected] = useState<StudentAccount | null>(null)
  const [pending, setPending] = useState<PendingSummary | null>(null)
  const [receipt, setReceipt] = useState<CredentialReceipt | null>(null)
  const [showPassword, setShowPassword] = useState(false)

  const clearUnauthorizedView = (failure: unknown) => {
    const status = failure && typeof failure === 'object' && 'status' in failure ? failure.status : null
    if (status !== 401 && status !== 403) return
    setList({ items: [], campuses: [] }); setCampusId(''); setSelected(null)
    setPassword(''); setResetPassword(''); setReceipt(null); setShowPassword(false)
  }

  const applyList = (next: AccountList) => {
    setList(next)
    setCampusId(current => next.campuses.some(c => c.campus_id === current) ? current : next.campuses.length === 1 ? next.campuses[0].campus_id : '')
    setSelected(current => current ? next.items.find(item => item.student_id === current.student_id) ?? null : null)
  }
  useEffect(() => {
    live.current = true
    void operations.list().then(next => { if (live.current) applyList(next) }).catch(e => { if (live.current) { clearUnauthorizedView(e); setError(errorMessage(e)) } }).finally(() => { if (live.current) setLoading(false) })
    return () => { live.current = false; operations.dispose() }
  }, [operations])

  const refresh = async () => {
    if (working.current) return
    working.current = true; setBusy(true); setError(''); setNotice('')
    try {
      const next = await operations.list()
      if (!live.current) return
      applyList(next)
      if (operations.pendingSummary?.kind === 'create') {
        const found = operations.observedCreation(next)
        setNotice(found ? `列表中已查到用户名「${found.username}」。这只能确认账号存在，仍请重试原操作确认本次创建及初始密码。` : '本次刷新尚未找到该用户名。创建结果仍未确认，请重试原操作。')
      }
    } catch (e) { if (live.current) { clearUnauthorizedView(e); setError(errorMessage(e)) } }
    finally { working.current = false; if (live.current) setBusy(false) }
  }
  const submit = async (action: () => Promise<CredentialReceipt>) => {
    if (working.current) return
    working.current = true; setBusy(true); setError(''); setNotice(''); setReceipt(null); setShowPassword(false)
    try {
      const result = await action()
      if (!live.current) return
      setReceipt(result); setPending(null); setSelected(null)
      if (result.kind === 'create') { setStudentName(''); setUsername('') }
      try { const next = await operations.list(); if (live.current) applyList(next) }
      catch (e) { if (live.current) { clearUnauthorizedView(e); setError(`账号操作已成功，列表暂未刷新：${errorMessage(e)}`) } }
    } catch (e) { if (live.current) { clearUnauthorizedView(e); setError(errorMessage(e)); setPending(operations.pendingSummary) } }
    finally { working.current = false; if (live.current) setBusy(false) }
  }
  const clearPending = () => {
    operations.discard(); setPending(null); setPassword(''); setResetPassword(''); setError('')
    setNotice('已清除本页的密码和重试信息。此前提交的结果仍需核对；如重置结果不明，请重新设置一个新密码。')
  }
  const filtered = list.items.filter(item => `${item.student_name} ${item.username}`.toLowerCase().includes(search.trim().toLowerCase()))
  const locked = busy || Boolean(pending)
  const campusLabel = (id: string) => list.campuses.length === 1 ? '当前授权校区' : id

  return <section className="admin-accounts" aria-labelledby="student-accounts-title">
    <div className="page-title-row"><div><span className="eyebrow">校区管理 · 学员名册</span><h1 id="student-accounts-title">一个账号，陪伴每一天。</h1><p>为学员建立长期登录账号。作业与订正记录跟随学员保存。</p></div><span className="admin-trust"><ShieldCheck size={18} /> 仅管理已授权校区</span></div>
    {error && <div className="message error" role="alert"><span>{error}</span></div>}
    {notice && <div className="message success" role="status"><span>{notice}</span></div>}
    {pending && <div className="admin-pending" role="status"><strong>{pending.studentName}的{pending.kind === 'create' ? '账号创建' : '密码重置'}尚未确认</strong><p>请先重试原操作。当前密码和请求只保留在本页；刷新或离开后会清除。账号出现在列表中，也不能证明本次密码已生效。</p><div className="admin-actions"><button className="button primary" disabled={busy} onClick={() => void submit(() => operations.retry())}><RefreshCw size={16} /> 重试原操作</button><button className="button secondary" disabled={busy} onClick={() => void refresh()}>核对账号列表</button><button className="text-button" disabled={busy} onClick={clearPending}>清除输入并结束本次操作</button></div></div>}
    {receipt && <section className="admin-receipt" aria-label="已确认的账号操作"><div className="admin-receipt-heading"><span><Check size={19} /> {receipt.studentName}的{receipt.kind === 'create' ? '账号已创建' : '密码已重置'}</span><button className="icon-button" aria-label="清除本次密码展示" onClick={() => { setReceipt(null); setShowPassword(false) }}><X size={18} /></button></div><p>请当面交给学员。使用此密码登录后，必须先设置自己的新密码。</p><div className="admin-credentials"><label>用户名<input readOnly value={receipt.username} aria-label="已创建账号的用户名" /></label><label>本次临时密码<span className="admin-password"><input readOnly type={showPassword ? 'text' : 'password'} value={receipt.password} autoComplete="off" aria-label="本次临时密码" /><button className="icon-button" aria-label={showPassword ? '隐藏临时密码' : '显示临时密码'} onClick={() => setShowPassword(value => !value)}>{showPassword ? <EyeOff size={17} /> : <Eye size={17} />}</button></span></label></div><small>密码仅在此页内存中展示，不会进入学员名册。交付后请清除展示。</small></section>}
    <div className="admin-grid"><section className="admin-roster" aria-label="学员账号列表"><div className="admin-panel-title"><div><Users size={19} /><h2>学员账号 <span>{list.items.length}</span></h2></div><button className="text-button" disabled={busy || loading} onClick={() => void refresh()}><RefreshCw size={15} /> 刷新</button></div><label className="admin-search"><Search size={17} /><input type="search" value={search} onChange={e => setSearch(e.target.value)} placeholder="搜索姓名或用户名" aria-label="搜索学员姓名或用户名" /></label>{loading ? <div className="admin-empty"><LoaderCircle className="spin" size={24} /><p>正在读取已授权学员…</p></div> : filtered.length ? <div className="admin-table-scroll"><table className="admin-table"><thead><tr><th>学员</th><th>用户名</th><th>账号状态</th><th><span className="sr-only">操作</span></th></tr></thead><tbody>{filtered.map(item => <tr key={item.student_id}><td><strong>{item.student_name}</strong>{list.campuses.length > 1 && <small>{campusLabel(item.campus_id)}</small>}</td><td className="admin-username">{item.username}</td><td><span className={`status-pill ${!item.active || item.must_change_password ? 'amber' : 'green'}`}>{!item.active ? '已停用' : item.must_change_password ? '首次登录需改密' : '可以登录'}</span></td><td><button className="text-button" disabled={locked || !item.active} onClick={() => { setSelected(item); setResetPassword(''); setReceipt(null); setError('') }}><KeyRound size={14} /> 重置密码</button></td></tr>)}</tbody></table></div> : <div className="admin-empty"><Users size={31} strokeWidth={1.3} /><h3>{search.trim() ? '没有找到对应学员' : '从第一位学员开始'}</h3><p>{search.trim() ? '试试姓名或完整用户名。' : '在右侧填写姓名与账号，完成后即可交给学员登录。'}</p></div>}</section>
      <aside className="admin-form-panel">{selected ? <><span className="eyebrow">重新交付登录密码</span><h2>为{selected.student_name}重置密码</h2><p className="admin-form-note">用户名仍是 <strong>{selected.username}</strong>。重置后原登录会话将失效，学员下一次登录需改密。</p><form onSubmit={event => { event.preventDefault(); const value = resetPassword; setResetPassword(''); void submit(() => operations.reset(selected, value)) }}><label htmlFor="admin-reset-password">新的临时密码</label><input id="admin-reset-password" type="password" value={resetPassword} onChange={e => setResetPassword(e.target.value)} autoComplete="new-password" required disabled={locked} /><p className="admin-field-note">15–128 个字符，支持空格。请勿使用学员个人信息作密码。</p><button className="button primary full" disabled={locked || !resetPassword}><KeyRound size={16} /> 确认重置密码</button><button className="text-button admin-cancel" type="button" disabled={locked} onClick={() => { setSelected(null); setResetPassword('') }}>返回创建账号</button></form></> : <><span className="eyebrow">建立长期学习档案</span><h2>添加一位学员</h2><p className="admin-form-note">姓名用于识别学员；用户名用于每次登录。先交付临时密码，再由学员设置自己的密码。</p><form onSubmit={event => { event.preventDefault(); const input = { student_name: studentName, username, campus_id: campusId, initial_password: password }; setPassword(''); void submit(() => operations.create(input)) }}><label htmlFor="admin-student-name">学员姓名</label><input id="admin-student-name" value={studentName} onChange={e => setStudentName(e.target.value)} maxLength={80} autoComplete="off" required disabled={locked} /><label htmlFor="admin-username">登录用户名</label><input id="admin-username" value={username} onChange={e => setUsername(e.target.value)} minLength={3} maxLength={64} autoComplete="off" autoCapitalize="none" spellCheck={false} required disabled={locked} /><p className="admin-field-note">3–64 位英文字母、数字或 . _ -，统一使用小写。</p><label htmlFor="admin-campus">所属校区</label><select id="admin-campus" value={campusId} onChange={e => setCampusId(e.target.value)} required disabled={locked || list.campuses.length === 1}><option value="">请选择已授权校区</option>{list.campuses.map(item => <option key={item.campus_id} value={item.campus_id}>{campusLabel(item.campus_id)}</option>)}</select><label htmlFor="admin-initial-password">初始临时密码</label><input id="admin-initial-password" type="password" value={password} onChange={e => setPassword(e.target.value)} autoComplete="new-password" required disabled={locked} /><p className="admin-field-note">15–128 个字符，首次登录必须更换。</p><button className="button primary full" disabled={locked || loading || !list.campuses.length}><Plus size={17} /> 创建学员账号</button>{!loading && !list.campuses.length && <p className="admin-field-note">当前没有可管理的校区，请先由系统管理员授予校区权限。</p>}</form></>}<div className="admin-form-foot"><ShieldCheck size={16} /><p>账号不会因换电脑而改变。忘记密码时，由已获授权的管理员协助重置。</p></div></aside></div>
    {busy && <div className="activity-indicator" role="status"><LoaderCircle className="spin" size={16} /> 正在核对账号操作，请稍等</div>}
  </section>
}
