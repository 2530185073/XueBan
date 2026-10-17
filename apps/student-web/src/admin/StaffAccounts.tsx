import { useEffect, useMemo, useRef, useState } from 'react'
import { Check, Eye, EyeOff, KeyRound, LoaderCircle, Plus, RefreshCw, ShieldCheck, UserCog, X } from 'lucide-react'
import { errorMessage } from './api'
import { STAFF_ROLES, StaffOperations, editable, roleLabel } from './staff'
import type { StaffAccount, StaffList, StaffPending, StaffReceipt, StaffResult, StaffRole } from './staff'
import type { AdminClient } from './types'
import './admin.css'

export function StaffAccounts({ client }: { client: AdminClient }) {
  const operations = useMemo(() => new StaffOperations(client), [client])
  const live = useRef(true)
  const working = useRef(false)
  const [list, setList] = useState<StaffList>({ items: [], campuses: [] })
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [username, setUsername] = useState('')
  const [campusIds, setCampusIds] = useState<string[]>([])
  const [steward, setSteward] = useState(false)
  const [password, setPassword] = useState('')
  const [resetPassword, setResetPassword] = useState('')
  const [selected, setSelected] = useState<StaffAccount | null>(null)
  const [mode, setMode] = useState<'access' | 'reset'>('access')
  const [accessCampuses, setAccessCampuses] = useState<string[]>([])
  const [accessSteward, setAccessSteward] = useState(false)
  const [accessActive, setAccessActive] = useState(true)
  const [pending, setPending] = useState<StaffPending | null>(null)
  const [receipt, setReceipt] = useState<StaffReceipt | null>(null)
  const [showPassword, setShowPassword] = useState(false)

  const clearUnauthorizedView = (failure: unknown) => {
    const status = failure && typeof failure === 'object' && 'status' in failure ? failure.status : null
    if (status !== 401 && status !== 403) return
    setList({ items: [], campuses: [] }); setCampusIds([]); setSelected(null)
    setPassword(''); setResetPassword(''); setReceipt(null); setShowPassword(false)
  }
  const applyList = (next: StaffList) => {
    setList(next)
    setCampusIds(current => {
      const kept = current.filter(id => next.campuses.some(c => c.campus_id === id))
      return kept.length || next.campuses.length !== 1 ? kept : [next.campuses[0].campus_id]
    })
    setSelected(current => current ? next.items.find(item => item.principal_id === current.principal_id) ?? null : null)
  }
  useEffect(() => {
    live.current = true
    void operations.list().then(next => { if (live.current) applyList(next) })
      .catch(e => { if (live.current) { clearUnauthorizedView(e); setError(errorMessage(e)) } })
      .finally(() => { if (live.current) setLoading(false) })
    return () => { live.current = false; operations.dispose() }
  }, [operations])

  const refresh = async () => {
    if (working.current) return
    working.current = true; setBusy(true); setError(''); setNotice('')
    try { const next = await operations.list(); if (live.current) applyList(next) }
    catch (e) { if (live.current) { clearUnauthorizedView(e); setError(errorMessage(e)) } }
    finally { working.current = false; if (live.current) setBusy(false) }
  }
  const submit = async (action: () => Promise<StaffResult>) => {
    if (working.current) return
    working.current = true; setBusy(true); setError(''); setNotice(''); setReceipt(null); setShowPassword(false)
    try {
      const result = await action()
      if (!live.current) return
      setPending(null); setSelected(null)
      if (result.kind === 'access') setNotice(`已更新 ${result.label} 的授权：${result.account.active ? '可以登录' : '已停用'}，校区 ${result.account.campus_ids.join('、')}。`)
      else setReceipt(result)
      if (result.kind === 'create') { setDisplayName(''); setUsername(''); setSteward(false) }
      try { const next = await operations.list(); if (live.current) applyList(next) }
      catch (e) { if (live.current) { clearUnauthorizedView(e); setError(`账号操作已成功，列表暂未刷新：${errorMessage(e)}`) } }
    } catch (e) { if (live.current) { clearUnauthorizedView(e); setError(errorMessage(e)); setPending(operations.pendingSummary) } }
    finally { working.current = false; if (live.current) setBusy(false) }
  }
  const clearPending = () => {
    operations.discard(); setPending(null); setPassword(''); setResetPassword(''); setError('')
    setNotice('已清除本页的密码和重试信息。此前提交的结果仍需核对；如重置结果不明，请重新设置一个新密码。')
  }
  const toggleCampus = (id: string) => setCampusIds(current => current.includes(id) ? current.filter(c => c !== id) : [...current, id])
  const choose = (item: StaffAccount, next: 'access' | 'reset') => {
    setSelected(item); setMode(next); setResetPassword(''); setReceipt(null); setError('')
    setAccessCampuses(item.campus_ids); setAccessSteward(item.roles.includes('data_steward')); setAccessActive(item.active)
  }
  const locked = busy || Boolean(pending)
  const roles: StaffRole[] = steward ? ['sitting_teacher', 'data_steward'] : ['sitting_teacher']

  return <section className="admin-accounts" aria-labelledby="staff-accounts-title">
    <div className="page-title-row"><div><span className="eyebrow">校区管理 · 老师账号</span><h1 id="staff-accounts-title">坐班老师的登录账号</h1><p>老师只能看到所授权校区的学员；首次登录必须设置自己的密码。</p></div><span className="admin-trust"><ShieldCheck size={18} /> 仅管理已授权校区</span></div>
    {error && <div className="message error" role="alert"><span>{error}</span></div>}
    {notice && <div className="message success" role="status"><span>{notice}</span></div>}
    {pending && <div className="admin-pending" role="status">
      <strong>{pending.label}的{{ create: '账号创建', reset: '密码重置', access: '授权修改' }[pending.kind]}尚未确认</strong>
      <p>请先重试原操作。当前密码和请求只保留在本页；刷新或离开后会清除。账号出现在列表中，也不能证明本次密码已生效。</p>
      <div className="admin-actions">
        <button className="button primary" disabled={busy} onClick={() => void submit(() => operations.retry())}><RefreshCw size={16} /> 重试原操作</button>
        <button className="button secondary" disabled={busy} onClick={() => void refresh()}>核对账号列表</button>
        <button className="text-button" disabled={busy} onClick={clearPending}>清除输入并结束本次操作</button>
      </div>
    </div>}
    {receipt && <section className="admin-receipt" aria-label="已确认的老师账号操作">
      <div className="admin-receipt-heading"><span><Check size={19} /> {receipt.label}的{receipt.kind === 'create' ? '账号已创建' : '密码已重置'}</span><button className="icon-button" aria-label="清除本次密码展示" onClick={() => { setReceipt(null); setShowPassword(false) }}><X size={18} /></button></div>
      <p>请当面或经安全渠道交给老师。使用此密码登录后，必须先设置自己的新密码。</p>
      <div className="admin-credentials">
        <label>用户名<input readOnly value={receipt.username} aria-label="老师账号用户名" /></label>
        <label>本次临时密码<span className="admin-password"><input readOnly type={showPassword ? 'text' : 'password'} value={receipt.password} autoComplete="off" aria-label="老师临时密码" /><button className="icon-button" aria-label={showPassword ? '隐藏临时密码' : '显示临时密码'} onClick={() => setShowPassword(value => !value)}>{showPassword ? <EyeOff size={17} /> : <Eye size={17} />}</button></span></label>
      </div>
      <small>密码仅在此页内存中展示。交付后请清除展示。</small>
    </section>}
    <div className="admin-grid">
      <section className="admin-roster" aria-label="老师账号列表">
        <div className="admin-panel-title"><div><UserCog size={19} /><h2>老师账号 <span>{list.items.length}</span></h2></div><button className="text-button" disabled={busy || loading} onClick={() => void refresh()}><RefreshCw size={15} /> 刷新</button></div>
        {loading ? <div className="admin-empty"><LoaderCircle className="spin" size={24} /><p>正在读取老师账号…</p></div>
          : list.items.length ? <div className="admin-table-scroll"><table className="admin-table"><thead><tr><th>用户名</th><th>角色与校区</th><th>账号状态</th><th><span className="sr-only">操作</span></th></tr></thead><tbody>
            {list.items.map(item => <tr key={item.principal_id}>
              <td className="admin-username">{item.username}</td>
              <td>{item.roles.map(roleLabel).join('、')}<small>{item.campus_ids.join('、')}</small></td>
              <td><span className={`status-pill ${!item.active || item.must_change_password ? 'amber' : 'green'}`}>{!item.active ? '已停用' : item.must_change_password ? '首次登录需改密' : '可以登录'}</span></td>
              <td>{editable(item) ? <span className="admin-row-actions"><button className="text-button" disabled={locked} onClick={() => choose(item, 'access')}><ShieldCheck size={14} /> 授权与状态</button><button className="text-button" disabled={locked || !item.active} onClick={() => choose(item, 'reset')}><KeyRound size={14} /> 重置密码</button></span> : <small>管理员账号，此处只读</small>}</td>
            </tr>)}
          </tbody></table></div>
          : <div className="admin-empty"><UserCog size={31} strokeWidth={1.3} /><h3>还没有老师账号</h3><p>在右侧为坐班老师建立账号。</p></div>}
      </section>
      <aside className="admin-form-panel">{selected && mode === 'access' ? <>
        <span className="eyebrow">授权与账号状态</span><h2>{selected.username}</h2>
        <p className="admin-form-note">减少校区、取消角色或停用后，该老师现有登录会话立即失效。停用的账号保留在列表中，可随时恢复。</p>
        <form onSubmit={event => { event.preventDefault(); const roles: StaffRole[] = accessSteward ? ['sitting_teacher', 'data_steward'] : ['sitting_teacher']; void submit(() => operations.updateAccess(selected, { campus_ids: accessCampuses, roles, active: accessActive })) }}>
          <fieldset className="admin-choices" disabled={locked}><legend>授权校区</legend>
            {list.campuses.map(c => <label key={c.campus_id}><input type="checkbox" checked={accessCampuses.includes(c.campus_id)} onChange={() => setAccessCampuses(current => current.includes(c.campus_id) ? current.filter(x => x !== c.campus_id) : [...current, c.campus_id])} /> {c.campus_id}</label>)}
          </fieldset>
          <fieldset className="admin-choices" disabled={locked}><legend>角色</legend>
            <label><input type="checkbox" checked readOnly disabled /> {STAFF_ROLES.sitting_teacher}</label>
            <label><input type="checkbox" checked={accessSteward} onChange={e => setAccessSteward(e.target.checked)} /> 同时授予{STAFF_ROLES.data_steward}</label>
          </fieldset>
          <fieldset className="admin-choices" disabled={locked}><legend>账号状态</legend>
            <label><input type="radio" name="staff-active" checked={accessActive} onChange={() => setAccessActive(true)} /> 可以登录</label>
            <label><input type="radio" name="staff-active" checked={!accessActive} onChange={() => setAccessActive(false)} /> 停用</label>
          </fieldset>
          <button className="button primary full" disabled={locked || !accessCampuses.length}><Check size={16} /> 保存授权</button>
          <button className="text-button admin-cancel" type="button" disabled={locked} onClick={() => setSelected(null)}>返回创建账号</button>
        </form>
      </> : selected ? <>
        <span className="eyebrow">重新交付登录密码</span><h2>为 {selected.username} 重置密码</h2>
        <p className="admin-form-note">重置后该老师现有登录会话失效，下一次登录需改密。</p>
        <form onSubmit={event => { event.preventDefault(); const value = resetPassword; setResetPassword(''); void submit(() => operations.reset(selected, value)) }}>
          <label htmlFor="staff-reset-password">新的临时密码</label>
          <input id="staff-reset-password" type="password" value={resetPassword} onChange={e => setResetPassword(e.target.value)} autoComplete="new-password" required disabled={locked} />
          <p className="admin-field-note">15–128 个字符，支持空格。</p>
          <button className="button primary full" disabled={locked || !resetPassword}><KeyRound size={16} /> 确认重置密码</button>
          <button className="text-button admin-cancel" type="button" disabled={locked} onClick={() => { setSelected(null); setResetPassword('') }}>返回创建账号</button>
        </form>
      </> : <>
        <span className="eyebrow">添加坐班老师</span><h2>新建老师账号</h2>
        <p className="admin-form-note">老师可以批改、处理求助和发布每日反馈；数据治理角色另可处理学生数据导出与删除。</p>
        <form onSubmit={event => { event.preventDefault(); const input = { display_name: displayName, username, campus_ids: campusIds, roles, initial_password: password }; setPassword(''); void submit(() => operations.create(input)) }}>
          <label htmlFor="staff-display-name">老师姓名</label>
          <input id="staff-display-name" value={displayName} onChange={e => setDisplayName(e.target.value)} maxLength={80} autoComplete="off" required disabled={locked} />
          <label htmlFor="staff-username">登录用户名</label>
          <input id="staff-username" value={username} onChange={e => setUsername(e.target.value)} minLength={3} maxLength={64} autoComplete="off" autoCapitalize="none" spellCheck={false} required disabled={locked} />
          <p className="admin-field-note">3–64 位英文字母、数字或 . _ -，统一使用小写。</p>
          <fieldset className="admin-choices" disabled={locked}><legend>授权校区</legend>
            {list.campuses.map(c => <label key={c.campus_id}><input type="checkbox" checked={campusIds.includes(c.campus_id)} onChange={() => toggleCampus(c.campus_id)} /> {c.campus_id}</label>)}
          </fieldset>
          <fieldset className="admin-choices" disabled={locked}><legend>角色</legend>
            <label><input type="checkbox" checked readOnly disabled /> {STAFF_ROLES.sitting_teacher}</label>
            <label><input type="checkbox" checked={steward} onChange={e => setSteward(e.target.checked)} /> 同时授予{STAFF_ROLES.data_steward}</label>
          </fieldset>
          <label htmlFor="staff-initial-password">初始临时密码</label>
          <input id="staff-initial-password" type="password" value={password} onChange={e => setPassword(e.target.value)} autoComplete="new-password" required disabled={locked} />
          <p className="admin-field-note">15–128 个字符，首次登录必须更换。</p>
          <button className="button primary full" disabled={locked || loading || !campusIds.length}><Plus size={17} /> 创建老师账号</button>
          {!loading && !list.campuses.length && <p className="admin-field-note">当前没有可管理的校区，请先由系统管理员授予校区权限。</p>}
        </form>
      </>}<div className="admin-form-foot"><ShieldCheck size={16} /><p>老师账号与学员账号分开管理；停用或改权限请联系系统管理员。</p></div></aside>
    </div>
    {busy && <div className="activity-indicator" role="status"><LoaderCircle className="spin" size={16} /> 正在核对账号操作，请稍等</div>}
  </section>
}
