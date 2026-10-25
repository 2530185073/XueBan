import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { LoginForm, PasswordForm, newPasswordIssue } from './AccountForms'
import type { Workspace } from '../useStudentWorkspace'

const workspace = { busy: '', account: { username: 'synthetic.student' } } as Workspace

describe('account forms', () => {
  it('uses standard password manager autocomplete and no pairing input', () => {
    const html = renderToStaticMarkup(<LoginForm workspace={workspace} />)
    expect(html).toContain('autoComplete="username"'); expect(html).toContain('autoComplete="current-password"')
    expect(html).not.toContain('配对'); expect(html).not.toContain('localStorage')
  })
  it('does not offer skipping a required password change', () => {
    const html = renderToStaticMarkup(<PasswordForm workspace={workspace} required onDone={() => {}} onCancel={() => {}} />)
    expect(html).toContain('autoComplete="new-password"'); expect(html).not.toContain('返回</button>')
    expect(html).not.toContain('初始密码</label>'); expect(html).toContain('保存密码，继续')
    expect(html).toContain('3–15 位英文字母或数字')
  })
  it('accepts simple letter or digit passwords and enforces the new policy', () => {
    expect(newPasswordIssue('abc', 'abc')).toBeNull()
    expect(newPasswordIssue('123456', '123456')).toBeNull()
    expect(newPasswordIssue('a1b2c3', 'a1b2c3')).toBeNull()
    expect(newPasswordIssue('ab', 'ab')).toContain('3–15')
    expect(newPasswordIssue('a'.repeat(16), 'a'.repeat(16))).toContain('3–15')
    expect(newPasswordIssue('中文密码', '中文密码')).toContain('英文字母或数字')
    expect(newPasswordIssue('abc123', 'abc124')).toContain('不一致')
  })
})
