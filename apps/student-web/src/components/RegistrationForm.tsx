import { useEffect, useRef, useState, type FormEvent } from 'react'
import { ArrowLeft, ArrowRight, BookOpen, Check, LoaderCircle } from 'lucide-react'
import type { Workspace } from '../useStudentWorkspace'
import type { RegistrationGrade, RegistrationOptions, RegistrationResult } from '../core/types'
import './registration.css'

const gradeLabels: Record<RegistrationGrade, string> = { 5: '五年级', 6: '六年级', 7: '初一（七年级）', 8: '初二（八年级）' }
type RegistrationIssue = { field: 'password' | 'confirm-password' | 'grade'; message: string }

export function registrationIssue(password: string, confirmation: string, grade: number, grades: readonly number[]): RegistrationIssue | null {
  if (password.length < 3 || password.length > 15 || /[^A-Za-z0-9]/.test(password)) return { field: 'password', message: '密码需要 3–15 位英文字母或数字，可以全用数字或全用字母。' }
  if (password !== confirmation) return { field: 'confirm-password', message: '两次密码不一致，请再核对一下。' }
  if (![5, 6, 7, 8].includes(grade) || !grades.includes(grade)) return { field: 'grade', message: '请选择自己当前的年级。' }
  return null
}

export function RegistrationForm({ workspace, options, onDone, onCancel }: {
  workspace: Workspace
  options: Extract<RegistrationOptions, { enabled: true }>
  onDone: (username: string) => void
  onCancel: () => void
}) {
  const [issue, setIssue] = useState<RegistrationIssue | null>(null)
  const [failed, setFailed] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [result, setResult] = useState<RegistrationResult | null>(null)
  const formRef = useRef<HTMLFormElement>(null)
  const headingRef = useRef<HTMLHeadingElement>(null)
  const issueRef = useRef<HTMLParagraphElement>(null)
  const pending = useRef(false)
  const lifecycle = useRef({ live: false })
  const disabled = submitting || !!workspace.busy

  useEffect(() => {
    const current = { live: true }
    lifecycle.current = current
    const form = formRef.current
    headingRef.current?.focus()
    return () => { current.live = false; form?.reset() }
  }, [])

  useEffect(() => {
    if (result) headingRef.current?.focus()
    else if (failed) issueRef.current?.focus()
  }, [result, failed])

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (pending.current || workspace.busy || result) return
    const form = event.currentTarget, data = new FormData(form)
    const password = String(data.get('password') ?? ''), confirmation = String(data.get('confirm-password') ?? '')
    const grade = Number(data.get('grade'))
    const invalid = registrationIssue(password, confirmation, grade, options.grades)
    setIssue(invalid); setFailed(false)
    if (invalid) { form.querySelector<HTMLElement>(`[name="${invalid.field}"]`)?.focus(); return }

    const current = lifecycle.current
    pending.current = true; setSubmitting(true)
    try {
      const registered = await workspace.register(password, confirmation, grade)
      if (!current.live) return
      if (registered) { form.reset(); setResult(registered) }
      else setFailed(true)
    } catch {
      if (current.live) setFailed(true)
    } finally {
      pending.current = false
      if (current.live) setSubmitting(false)
    }
  }

  if (result) return <section className="auth-card registration-card" aria-labelledby="registration-heading">
    <span className="tiny-tab">自己的账号，自己的作业本</span>
    <div className="auth-icon"><Check size={29} strokeWidth={1.5} /></div>
    <h2 ref={headingRef} id="registration-heading" tabIndex={-1}>注册成功</h2>
    <p>请记住下面的账号，以后用它和刚设置的密码登录。</p>
    <div className="registration-account" role="status"><span>你的账号</span><strong>{result.username}</strong><small>{gradeLabels[result.grade]} · 知识范围按所选年级设置</small></div>
    <p>账号已创建，请返回登录。</p>
    <button type="button" className="button primary full" onClick={() => onDone(result.username)}>记住了，返回登录 <ArrowRight size={18} /></button>
  </section>

  return <section className="auth-card registration-card" aria-labelledby="registration-heading">
    <span className="tiny-tab">学生账号 · 临时开放</span>
    <div className="auth-icon"><BookOpen size={29} strokeWidth={1.5} /></div>
    <h2 ref={headingRef} id="registration-heading" tabIndex={-1}>注册学生账号</h2>
    <p>账号由系统依次分配。设置密码，再选择你现在的年级。</p>
    <div className="registration-account"><span>账号预览</span><strong>{options.username_preview}</strong><small>预览账号尚未保留，实际账号以注册成功后显示的为准。</small></div>
    <form ref={formRef} noValidate aria-busy={submitting} onSubmit={event => void submit(event)}>
      <label htmlFor="registration-password">设置密码</label>
      <input id="registration-password" name="password" type="password" autoComplete="new-password" autoCapitalize="none" spellCheck={false} minLength={3} maxLength={15} pattern="[A-Za-z0-9]{3,15}" required disabled={disabled} aria-invalid={issue?.field === 'password' || undefined} aria-describedby={`registration-password-guidance${issue?.field === 'password' ? ' registration-issue' : ''}`} />
      <small id="registration-password-guidance" className="password-guidance">3–15 位英文字母或数字，可以全用数字或全用字母。</small>
      <label htmlFor="registration-confirm-password">再输入一次密码</label>
      <input id="registration-confirm-password" name="confirm-password" type="password" autoComplete="new-password" autoCapitalize="none" spellCheck={false} minLength={3} maxLength={15} pattern="[A-Za-z0-9]{3,15}" required disabled={disabled} aria-invalid={issue?.field === 'confirm-password' || undefined} aria-describedby={issue?.field === 'confirm-password' ? 'registration-issue' : undefined} />
      <label htmlFor="registration-grade">当前年级（必选）</label>
      <select id="registration-grade" name="grade" defaultValue="" required disabled={disabled} aria-invalid={issue?.field === 'grade' || undefined} aria-describedby={`registration-grade-guidance${issue?.field === 'grade' ? ' registration-issue' : ''}`}>
        <option value="" disabled>请选择年级</option>
        {options.grades.map(grade => <option key={grade} value={grade}>{gradeLabels[grade]}</option>)}
      </select>
      <small id="registration-grade-guidance" className="password-guidance">知识点与学习内容将按你选择的年级设置。</small>
      {issue && <p id="registration-issue" className="form-issue" role="alert">{issue.message}</p>}
      {failed && <p ref={issueRef} className="form-issue" role="alert" tabIndex={-1}>注册结果暂未确认。请保留当前页面，用相同内容重试；如果需要帮助，请联系坐班老师。</p>}
      <button className="button primary full" disabled={disabled}>{submitting ? <LoaderCircle className="spin" size={18} /> : <ArrowRight size={18} />} {submitting ? '正在注册…' : failed ? '重试注册' : '创建账号'}</button>
    </form>
    <button type="button" className="text-button reconnect" disabled={disabled} onClick={onCancel}><ArrowLeft size={15} /> 取消，返回登录</button>
  </section>
}
