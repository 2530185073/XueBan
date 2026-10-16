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
    expect(html).toContain('初始密码'); expect(html).toContain('保存密码，继续')
  })
  it('preserves spaces and counts Unicode characters while enforcing confirmation', () => {
    expect(newPasswordIssue('中文'.repeat(8), '中文'.repeat(8))).toBeNull()
    expect(newPasswordIssue('🙂'.repeat(15), '🙂'.repeat(15))).toBeNull()
    expect(newPasswordIssue('  my synthetic password  ', '  my synthetic password  ')).toBeNull()
    expect(newPasswordIssue('short', 'short')).not.toBeNull()
    expect(newPasswordIssue('synthetic password one', 'synthetic password two')).toContain('不一致')
  })
})
