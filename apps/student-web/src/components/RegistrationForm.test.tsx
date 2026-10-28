import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'
import type { RegistrationOptions } from '../core/types'
import type { Workspace } from '../useStudentWorkspace'
import { RegistrationForm, registrationIssue } from './RegistrationForm'

const options: Extract<RegistrationOptions, { enabled: true }> = { enabled: true, username_preview: '荣耀学员001', grades: [5, 6, 7, 8] }
const workspace = { busy: '', register: vi.fn(), login: vi.fn(), clearError: vi.fn() } as unknown as Workspace
const markup = (current = workspace, available = options) => renderToStaticMarkup(<RegistrationForm workspace={current} options={available} onDone={() => {}} onCancel={() => {}} />)

describe('registration form', () => {
  it('shows the server preview as advisory, without an editable or submitted username', () => {
    const html = markup()
    expect(html).toContain('荣耀学员001')
    expect(html).toContain('预览账号尚未保留')
    expect(html).toContain('实际账号以注册成功后显示的为准')
    expect(html).not.toContain('name="username"')
    expect(html).not.toContain('荣耀学院')
    expect(html).not.toContain('localStorage')
    expect(html).not.toContain('sessionStorage')
  })

  it('uses each refreshed server preview without deriving a local sequence', () => {
    const html = markup(workspace, { ...options, username_preview: '荣耀学员002' })
    expect(html).toContain('荣耀学员002')
    expect(html).not.toContain('荣耀学员001')
  })

  it('requires an explicit grade and explains the knowledge scope', () => {
    const html = markup()
    expect(html).toMatch(/<select[^>]*name="grade"[^>]*required=""/)
    expect(html).toContain('<option value="" disabled="" selected="">请选择年级</option>')
    for (const [grade, label] of [[5, '五年级'], [6, '六年级'], [7, '初一（七年级）'], [8, '初二（八年级）']] as const) expect(html).toContain(`<option value="${grade}">${label}</option>`)
    expect(html).toContain('知识点与学习内容将按你选择的年级设置')
    expect(html.match(/<option\b/g)).toHaveLength(5)
  })

  it('only offers grades present in server metadata', () => {
    const html = markup(workspace, { ...options, grades: [6, 8] })
    expect(html).toContain('value="6"'); expect(html).toContain('value="8"')
    expect(html).not.toContain('value="5"'); expect(html).not.toContain('value="7"')
  })

  it('offers two new-password fields with matching ASCII constraints and no preset password', () => {
    const html = markup()
    const inputs = html.match(/<input\b[^>]*>/g) ?? []
    expect(inputs).toHaveLength(2)
    for (const input of inputs) {
      expect(input).toContain('type="password"')
      expect(input).toContain('autoComplete="new-password"')
      expect(input).toContain('minLength="3"'); expect(input).toContain('maxLength="15"')
      expect(input).toContain('pattern="[A-Za-z0-9]{3,15}"')
      expect(input).toContain('required=""')
      expect(input).not.toContain('value=')
    }
    expect(html).toContain('取消，返回登录')
    expect(html).toContain('创建账号')
    expect(workspace.login).not.toHaveBeenCalled()
  })

  it('disables form controls and cancel while the workspace is busy', () => {
    const html = markup({ ...workspace, busy: '正在注册' })
    for (const control of html.match(/<(?:input|select|button)\b[^>]*>/g) ?? []) expect(control).toContain('disabled=""')
  })
})

describe('registration validation', () => {
  it.each(['123', 'abc', 'ABC', 'Ab3', '123456789012345', 'abcdefghijklmno', 'ABCDEFGHIJKLMNO', 'Ab3'.repeat(5)])('accepts ASCII password %s', password => {
    for (const grade of [5, 6, 7, 8]) expect(registrationIssue(password, password, grade, options.grades)).toBeNull()
  })

  it.each(['', '12', 'a'.repeat(16), ' abc', 'abc ', 'a b', 'abc!', 'ab_c', '中文密码', '１２３', 'ＡＢＣ', 'Abé', 'Аbc', 'ab🙂', 'abc\n', 'abc\r', 'abc\t', 'abc\u2028', 'abc\u2029'])('rejects invalid password %j at the password field', password => {
    expect(registrationIssue(password, password, 5, options.grades)?.field).toBe('password')
  })

  it.each(['', 'ab3', 'Ab4', 'Ab3 ', 'Ab3\n'])('requires exact password confirmation %j', confirmation => {
    const issue = registrationIssue('Ab3', confirmation, 6, options.grades)
    expect(issue?.field).toBe('confirm-password')
    expect(issue?.message).toContain('不一致')
  })

  it.each([0, 4, 9, -1, 5.5, NaN, Infinity])('rejects missing or unsupported grade %s', grade => {
    expect(registrationIssue('123', '123', grade, options.grades)?.field).toBe('grade')
  })

  it('requires the selected grade to be in the current server options', () => {
    expect(registrationIssue('123', '123', 5, [6, 7, 8])?.field).toBe('grade')
    expect(registrationIssue('123', '123', 6, [])?.field).toBe('grade')
    expect(registrationIssue('123', '123', 9, [9])?.field).toBe('grade')
  })
})
