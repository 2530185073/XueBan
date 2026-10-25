import { useState, type FormEvent } from 'react'
import { ArrowLeft, ArrowRight, BookOpen, KeyRound, Leaf, LoaderCircle } from 'lucide-react'
import type { Workspace } from '../useStudentWorkspace'

export function newPasswordIssue(password: string, confirmation: string): string | null {
  if ([...password].length < 15 || [...password].length > 128 || new TextEncoder().encode(password).length > 512) return '新密码需要 15–128 个字符，可以是一句好记的话。'
  if (password !== confirmation) return '两次新密码不一致，请再核对一下。'
  return null
}

export function LoginForm({ workspace }: { workspace: Workspace }) {
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const form = event.currentTarget, data = new FormData(form)
    if (await workspace.login(String(data.get('username') ?? ''), String(data.get('password') ?? ''))) form.reset()
  }
  return <section className="auth-card"><span className="tiny-tab">打开我的作业本</span><div className="auth-icon"><BookOpen size={29} strokeWidth={1.5} /></div><h2>你好，欢迎回来</h2><p>用老师为你开通的账号登录。<br />换一台设备，也能找到自己的作业。</p><form onSubmit={event => void submit(event)}><label htmlFor="login-username">账号</label><input id="login-username" name="username" autoComplete="username" autoCapitalize="none" spellCheck={false} placeholder="请输入自己的账号" disabled={!!workspace.busy} required maxLength={64} /><label htmlFor="login-password">密码</label><input id="login-password" name="password" type="password" autoComplete="current-password" placeholder="请输入密码" disabled={!!workspace.busy} required /><button className="button primary full" disabled={!!workspace.busy}>{workspace.busy ? <LoaderCircle className="spin" size={18} /> : <ArrowRight size={18} />} {workspace.busy || '打开我的作业本'}</button></form><p className="account-assistance">还没有账号或忘记密码？请找坐班老师帮忙。</p><div className="auth-bottom"><Leaf size={15} /><span>一个账号，留住每一次认真思考。</span></div></section>
}

export function PasswordForm({ workspace, required, onDone, onCancel }: { workspace: Workspace; required: boolean; onDone: () => void; onCancel: () => void }) {
  const [issue, setIssue] = useState<string | null>(null)
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const form = event.currentTarget, data = new FormData(form)
    const next = String(data.get('new-password') ?? ''), invalid = newPasswordIssue(next, String(data.get('confirm-password') ?? ''))
    setIssue(invalid); if (invalid) return
    if (await workspace.changePassword(String(data.get('current-password') ?? ''), next)) { form.reset(); onDone() }
  }
  return <section className="password-page"><div className="password-intro"><span className="eyebrow">自己的账号，自己的作业本</span><h1>{required ? '先设置一个自己的密码。' : '给作业本，换一把钥匙。'}</h1><p>{required ? '老师给你的初始密码只用于第一次登录。设置好新密码，就可以开始今天的作业。' : '更换密码后，原来的作业和订正记录仍然保留。'}</p></div><div className="auth-card"><div className="auth-icon"><KeyRound size={27} strokeWidth={1.5} /></div><h2>{required ? '设置新密码' : '修改密码'}</h2><p>当前账号：<strong>{workspace.account?.username}</strong></p><form onSubmit={event => void submit(event)}><input type="text" name="username" autoComplete="username" value={workspace.account?.username ?? ''} readOnly hidden /><label htmlFor="current-password">{required ? '初始密码' : '当前密码'}</label><input id="current-password" name="current-password" type="password" autoComplete="current-password" required disabled={!!workspace.busy} /><label htmlFor="new-password">新密码</label><input id="new-password" name="new-password" type="password" autoComplete="new-password" aria-describedby="password-guidance" required disabled={!!workspace.busy} /><small id="password-guidance" className="password-guidance">15–128 个字符，可以用一句自己记得住的话，空格也会保留。</small><label htmlFor="confirm-password">再输入一次新密码</label><input id="confirm-password" name="confirm-password" type="password" autoComplete="new-password" required disabled={!!workspace.busy} />{issue && <p className="form-issue" role="alert">{issue}</p>}<button className="button primary full" disabled={!!workspace.busy}>{workspace.busy ? <LoaderCircle className="spin" size={18} /> : <ArrowRight size={18} />} {workspace.busy || (required ? '保存密码，继续' : '保存新密码')}</button></form>{!required && <button className="text-button reconnect" disabled={!!workspace.busy} onClick={onCancel}><ArrowLeft size={15} /> 返回</button>}<p className="account-assistance">如果保存时断网，请用相同内容重试。刷新后，可先尝试用新密码登录。</p></div></section>
}
