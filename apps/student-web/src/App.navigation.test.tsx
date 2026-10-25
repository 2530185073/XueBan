import { renderToStaticMarkup } from 'react-dom/server'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { Workspace } from './useStudentWorkspace'
import type { DraftView } from './core/types'
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

describe('homework observation controls', () => {
  const work = (status: 'queued' | 'running' | 'retryable_failed', paused = false) => {
    state.workspace.draft = { id: 'draft_one', title: '学校作业', stage: 'waiting', createdAt: '2026-10-17T00:00:00Z', updatedAt: '2026-10-17T00:00:00Z', homeworkId: 'homework_one', error: null,
      pageCount: 0, uploadedCount: 0, photo: null, pages: [], editable: false, detail: null,
      observation: { status, paused, phase: 'recognizing', lastProgressAt: 0, jobs: {} } } satisfies DraftView
  }
  it('shows queued work without spinning or suggesting another submission', () => {
    work('queued'); state.workspace.busy = '正在检查作业'
    const html = renderToStaticMarkup(<App />)
    expect(html).toContain('排队等待处理')
    expect(html).not.toContain('读题与作答')
    expect(html).not.toContain(' spin')
    expect(html).not.toContain('继续未完成的检查')
    expect(html).not.toContain('重试失败任务')
  })
  it('offers a read-only refresh and explicit continuation after timeout while preserving account navigation', () => {
    work('running', true)
    const html = renderToStaticMarkup(<App />)
    expect(html).toContain('进度已保留')
    expect(html).toContain('服务可能暂不可用')
    expect(html).toContain('刷新状态')
    expect(html).toContain('继续未完成的检查')
    expect(html).not.toContain('重试失败任务')
    expect(html).not.toContain(' spin')
    expect(navigation(html)).toContain('历史记录')
  })
  it('uses processing animation only for running work and separates terminal retry', () => {
    work('running')
    expect(renderToStaticMarkup(<App />)).toContain('读题与作答')
    expect(renderToStaticMarkup(<App />)).toContain(' spin')
    state.workspace.draft!.stage = 'failed'
    const html = renderToStaticMarkup(<App />)
    expect(html).toContain('重试失败任务')
    expect(html).not.toContain('继续未完成的检查')
  })
})
const navigation = (html: string) => html.match(/<nav aria-label="主要导航">[\s\S]*?<\/nav>/)?.[0]
describe('account initialization navigation barrier', () => {
  it('renders four sibling navigation buttons without group spacers', () => {
    const nav = navigation(renderToStaticMarkup(<App />))!
    expect(nav.match(/<button\b/g)).toHaveLength(4)
    expect(nav).not.toMatch(/nav-group|nav-divider|<span/)
    expect(nav.match(/aria-current="page"/g)).toHaveLength(1)
  })
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
