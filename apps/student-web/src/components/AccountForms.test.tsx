import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'
import { LoginForm, PasswordForm, newPasswordIssue } from './AccountForms'
import type { Workspace } from '../useStudentWorkspace'

const workspace = {
  busy: '', account: { username: 'synthetic.student', student_id: 'student_one', must_change_password: false },
  client: { registrationOptions: vi.fn().mockResolvedValue({ enabled: false, username_preview: null, grades: [] }) },
  register: vi.fn(), login: vi.fn(), clearError: vi.fn(),
} as unknown as Workspace
const adminWorkspace = { busy: '', account: { username: 'synthetic.admin', student_id: null, must_change_password: false } } as Workspace

describe('account forms', () => {
  it('uses standard password manager autocomplete and no pairing input', () => {
    const html = renderToStaticMarkup(<LoginForm workspace={workspace} />)
    expect(html).toContain('autoComplete="username"'); expect(html).toContain('autoComplete="current-password"')
    expect(html).not.toContain('配对'); expect(html).not.toContain('localStorage')
  })
  it('keeps login available while registration availability is being checked', () => {
    const html = renderToStaticMarkup(<LoginForm workspace={workspace} />)
    expect(html).toContain('正在查看注册入口')
    expect(html).toContain('id="login-username"')
    expect(html).toContain('id="login-password"')
    expect(html).not.toContain('注册学生账号')
    expect(html).not.toContain('disabled=""')
  })
  it('does not offer skipping a required password change', () => {
    const html = renderToStaticMarkup(<PasswordForm workspace={workspace} required onDone={() => {}} onCancel={() => {}} />)
    expect(html).toContain('autoComplete="new-password"'); expect(html).not.toContain('返回</button>')
    expect(html).toContain('初始密码'); expect(html).toContain('保存密码，继续')
  })
  it('offers the current password field and a return button for an optional change', () => {
    const html = renderToStaticMarkup(<PasswordForm workspace={workspace} required={false} onDone={() => {}} onCancel={() => {}} />)
    expect(html).toContain('<label for="current-password">当前密码</label>')
    expect(html).toContain('保存新密码'); expect(html).toContain('返回</button>')
  })

  describe.each([
    ['student', workspace],
    ['administrator', adminWorkspace],
  ] as const)('%s password form', (_accountType, accountWorkspace) => {
    it.each([
      [true, '初始密码'],
      [false, '当前密码'],
    ] as const)('keeps the existing password field unrestricted when required=%s', (required, label) => {
      const html = renderToStaticMarkup(<PasswordForm workspace={accountWorkspace} required={required} onDone={() => {}} onCancel={() => {}} />)
      const currentPassword = html.match(/<input\b[^>]*id="current-password"[^>]*>/)?.[0]
      expect(html).toContain(`<label for="current-password">${label}</label>`)
      expect(currentPassword).toContain('name="current-password"')
      expect(currentPassword).toContain('type="password"')
      expect(currentPassword).toContain('autoComplete="current-password"')
      expect(currentPassword).toContain('required=""')
      expect(currentPassword).not.toMatch(/\b(?:minLength|maxLength|pattern)=/i)
    })
  })

  it.each([true, false])('shows the simple policy for an account with a student_id when required=%s', required => {
    const html = renderToStaticMarkup(<PasswordForm workspace={workspace} required={required} onDone={() => {}} onCancel={() => {}} />)
    expect(html).toContain('3–15')
    expect(html).toContain('字母'); expect(html).toContain('数字')
    expect(html).not.toContain('15–128'); expect(html).not.toContain('空格也会保留')
  })

  it.each([true, false])('keeps the strong policy for an account with student_id=null when required=%s', required => {
    const html = renderToStaticMarkup(<PasswordForm workspace={adminWorkspace} required={required} onDone={() => {}} onCancel={() => {}} />)
    expect(html).toContain('15–128')
    expect(html).toContain('空格也会保留')
    expect(html).not.toContain('3–15')
  })
})

describe('student new password policy', () => {
  it.each([
    ['3-character mixed minimum', 'Ab3'],
    ['15-character mixed maximum', 'Ab3'.repeat(5)],
    ['3 digits', '123'],
    ['15 digits', '123456789012345'],
    ['3 lowercase letters', 'abc'],
    ['15 lowercase letters', 'abcdefghijklmno'],
    ['3 uppercase letters', 'ABC'],
    ['15 uppercase letters', 'ABCDEFGHIJKLMNO'],
  ])('accepts %s by default and with student=true', (_name, password) => {
    expect(newPasswordIssue(password, password)).toBeNull()
    expect(newPasswordIssue(password, password, true)).toBeNull()
  })

  it.each([
    ['empty password', ''],
    ['2 characters', 'A1'],
    ['16 characters', 'Ab3'.repeat(5) + 'x'],
    ['leading space', ' Ab3'],
    ['trailing space', 'Ab3 '],
    ['embedded space', 'Ab 3'],
    ['15 characters with a space', 'A'.repeat(14) + ' '],
    ['punctuation', 'Ab3!'],
    ['underscore', 'Ab_3'],
    ['hyphen', 'Ab-3'],
    ['Chinese characters', '中文密码'],
    ['15 Chinese characters', '中'.repeat(15)],
    ['accented Latin letter', 'Abé3'],
    ['non-ASCII lookalike letter', 'Аb3'],
    ['full-width letters', 'ＡＢＣ'],
    ['full-width digits', '１２３'],
    ['emoji', 'Ab🙂3'],
    ['tab', 'Ab\t3'],
    ['embedded newline', 'Ab\n3'],
    ['trailing newline', 'Ab3\n'],
    ['trailing carriage return', 'Ab3\r'],
    ['trailing CRLF', 'Ab3\r\n'],
    ['trailing Unicode line separator', 'Ab3\u2028'],
    ['trailing Unicode paragraph separator', 'Ab3\u2029'],
  ])('rejects %s by default and with student=true', (_name, password) => {
    expect(newPasswordIssue(password, password)).not.toBeNull()
    expect(newPasswordIssue(password, password, true)).not.toBeNull()
  })

  it.each([
    ['Ab3', 'Ab4'],
    ['Ab3', 'ab3'],
    ['123', '124'],
    ['Ab3', ''],
    ['Ab3', 'Ab3 '],
    ['Ab3', 'Ab3\n'],
  ])('requires an exact confirmation for %j and %j', (password, confirmation) => {
    expect(newPasswordIssue(password, confirmation)).toContain('不一致')
    expect(newPasswordIssue(password, confirmation, true)).toContain('不一致')
  })
})

describe('administrator new password policy', () => {
  it.each([
    ['15-character minimum', 'a'.repeat(15)],
    ['128-character maximum', 'a'.repeat(128)],
    ['Chinese characters', '中文'.repeat(8)],
    ['15 Unicode code points', '🙂'.repeat(15)],
    ['128 Unicode code points and 512 UTF-8 bytes', '🙂'.repeat(128)],
    ['leading and trailing spaces', '  my synthetic password  '],
    ['symbols', 'synthetic!@#password'],
  ])('accepts %s with student=false', (_name, password) => {
    expect(newPasswordIssue(password, password, false)).toBeNull()
  })

  it.each([
    ['empty password', ''],
    ['student-length password', 'Ab3'],
    ['14 characters', 'a'.repeat(14)],
    ['129 characters', 'a'.repeat(129)],
    ['129 Unicode code points', '🙂'.repeat(129)],
  ])('rejects %s with student=false', (_name, password) => {
    expect(newPasswordIssue(password, password, false)).not.toBeNull()
  })

  it('preserves spaces and enforces exact confirmation with student=false', () => {
    expect(newPasswordIssue('synthetic password one', 'synthetic password two', false)).toContain('不一致')
    expect(newPasswordIssue('  my synthetic password  ', 'my synthetic password', false)).toContain('不一致')
  })
})
