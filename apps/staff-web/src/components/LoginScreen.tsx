import { useState, type FormEvent } from 'react'
import { ArrowRight, Eye, KeyRound, LoaderCircle, LockKeyhole, LogOut } from 'lucide-react'

export function newPasswordIssue(password: string, confirmation: string): string | null {
  if ([...password].length < 15 || [...password].length > 128 || new TextEncoder().encode(password).length > 512) return '新密码需要 15–128 个字符，可以用一句好记的话。'
  if (password !== confirmation) return '两次输入的新密码不一致。'
  return null
}

export function LoginScreen({ busy, onLogin, onSample }: { busy: boolean; onLogin: (username: string, password: string) => Promise<boolean>; onSample: () => void }) {
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const form = event.currentTarget, data = new FormData(form)
    if (await onLogin(String(data.get('username') ?? ''), String(data.get('password') ?? ''))) form.reset()
  }
  return <section className="auth-card" aria-labelledby="login-title">
    <span className="round-icon"><LockKeyhole size={24} /></span>
    <h2 id="login-title">坐班老师登录</h2>
    <p>使用校区管理员为你开通的老师账号。登录状态只保留在本页内存中，关闭或刷新页面后需重新登录。</p>
    <form onSubmit={event => void submit(event)}>
      <label>账号<input name="username" autoComplete="username" autoCapitalize="none" spellCheck={false} required maxLength={64} disabled={busy} placeholder="老师账号" /></label>
      <label>密码<input name="password" type="password" autoComplete="current-password" required maxLength={128} disabled={busy} placeholder="密码" /></label>
      <button className="button primary full" disabled={busy}>{busy ? <LoaderCircle size={16} className="spin" /> : <ArrowRight size={16} />}{busy ? '正在登录…' : '登录工作台'}</button>
    </form>
    <p className="auth-assist">没有账号或忘记密码？请联系校区管理员重置。</p>
    <button className="button text-button full" disabled={busy} onClick={onSample}><Eye size={16} />只看合成样例，不登录</button>
  </section>
}

export function PasswordScreen({ busy, username, onChange, onLogout }: { busy: boolean; username: string; onChange: (current: string, next: string) => Promise<boolean>; onLogout: () => void }) {
  const [issue, setIssue] = useState<string | null>(null)
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const form = event.currentTarget, data = new FormData(form)
    const next = String(data.get('new-password') ?? '')
    const invalid = newPasswordIssue(next, String(data.get('confirm-password') ?? ''))
    setIssue(invalid); if (invalid) return
    if (await onChange(String(data.get('current-password') ?? ''), next)) form.reset()
  }
  return <section className="auth-card" aria-labelledby="password-title">
    <span className="round-icon"><KeyRound size={24} /></span>
    <h2 id="password-title">先设置自己的密码</h2>
    <p>账号 <strong>{username}</strong> 正在使用初始密码。设置新密码后才能查看学生作业。</p>
    <form onSubmit={event => void submit(event)}>
      <input type="text" name="username" autoComplete="username" value={username} readOnly hidden />
      <label>初始密码<input name="current-password" type="password" autoComplete="current-password" required disabled={busy} /></label>
      <label>新密码<input name="new-password" type="password" autoComplete="new-password" required disabled={busy} aria-describedby="password-rule" /></label>
      <small id="password-rule" className="auth-rule">15–128 个字符，空格也会保留。</small>
      <label>再输入一次新密码<input name="confirm-password" type="password" autoComplete="new-password" required disabled={busy} /></label>
      {issue && <p className="form-issue" role="alert">{issue}</p>}
      <button className="button primary full" disabled={busy}>{busy ? <LoaderCircle size={16} className="spin" /> : <ArrowRight size={16} />}{busy ? '正在保存…' : '保存新密码并进入'}</button>
    </form>
    <button className="button text-button full" disabled={busy} onClick={onLogout}><LogOut size={16} />退出登录</button>
  </section>
}
