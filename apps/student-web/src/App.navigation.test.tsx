import { renderToStaticMarkup } from 'react-dom/server'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { Workspace } from './useStudentWorkspace'
import App from './App'

const state = vi.hoisted(() => ({ workspace: {} as Workspace }))
vi.mock('./useStudentWorkspace', () => ({ useStudentWorkspace: () => state.workspace }))
beforeEach(() => {
  state.workspace = {
    client: {}, loading: false, sessionInitializing: false, busy: '', online: true,
    account: { username: 'synthetic.student', student_id: 'student_one', must_change_password: false },
    identity: { actor_id: 'actor_one', roles: ['student'], authorized_students: [{ student_id: 'student_one', display_name: '合成学生', campus_id: 'campus_one' }] },
    studentId: 'student_one', draft: null, detail: null, drafts: [], recordVersion: 0,
  } as unknown as Workspace
})
const navigation = (html: string) => html.match(/<nav aria-label="主要导航">[\s\S]*?<\/nav>/)?.[0]
describe('account initialization navigation barrier', () => {
  it('does not expose history navigation when identity is ready but login restoration is still running', () => {
    state.workspace.sessionInitializing = true; state.workspace.busy = '正在登录'
    const html = renderToStaticMarkup(<App />)
    expect(navigation(html)).toBeUndefined()
    expect(html).toContain('正在打开你的作业本')
    expect(html).not.toContain('今天的作业，一起看看')
    state.workspace.sessionInitializing = false; state.workspace.busy = ''
    expect(navigation(renderToStaticMarkup(<App />))).toContain('历史记录')
  })
  it('also waits for initial session restoration on browser reload', () => {
    state.workspace.loading = true
    expect(navigation(renderToStaticMarkup(<App />))).toBeUndefined()
  })
  it('keeps navigation available during model work rather than locking the student on a waiting result', () => {
    state.workspace.busy = '正在检查作业'
    const nav = navigation(renderToStaticMarkup(<App />))
    expect(nav).toContain('我的作业'); expect(nav).toContain('历史记录'); expect(nav).toContain('知识图谱')
    expect(nav).not.toContain('disabled')
  })
})
