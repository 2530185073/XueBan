import { useCallback, useEffect, useId, useState, type FormEvent } from 'react'
import { ArrowLeft, LoaderCircle, Plus, RefreshCw, Undo2 } from 'lucide-react'
import { MEMORY_CONTENT_MAX, MEMORY_CONTENT_MIN, safeError, type MemoryList, type StudentMemoryCategory } from '../core'
import type { Workspace } from '../useStudentWorkspace'
import { dayLabel, memoryCategoryLabels, profileMemories } from '../learning'

const placeholders: Record<StudentMemoryCategory, string> = { interface_preference: '例如：提示先给图示，再给文字。', learning_goal: '例如：这周订正时，先自己检查一遍再求助。' }

export function MemoryForm({ disabled, onSubmit }: { disabled: boolean; onSubmit: (category: StudentMemoryCategory, content: string) => Promise<boolean> }) {
  const [category, setCategory] = useState<StudentMemoryCategory>('interface_preference')
  const [content, setContent] = useState('')
  const id = useId(), length = [...content.trim()].length
  const submit = async (event: FormEvent) => { event.preventDefault(); if (await onSubmit(category, content)) setContent('') }
  return <form className="memory-form" onSubmit={event => void submit(event)}>
    <fieldset><legend>想让学伴记住什么</legend><div className="segmented">{(['interface_preference', 'learning_goal'] as const).map(value => <button key={value} type="button" aria-pressed={category === value} className={category === value ? 'active' : ''} onClick={() => setCategory(value)}>{memoryCategoryLabels[value]}</button>)}</div></fieldset>
    <label htmlFor={id}>{category === 'learning_goal' ? '写一个自己的小目标' : '写一个使用学伴的偏好'}</label>
    <textarea id={id} rows={2} maxLength={MEMORY_CONTENT_MAX} value={content} placeholder={placeholders[category]} aria-describedby={`${id}-count`} disabled={disabled} onChange={event => setContent(event.target.value)} />
    <div className="appeal-form-foot"><span id={`${id}-count`} className="muted small">{length}/{MEMORY_CONTENT_MAX} · 随时可以撤回</span><button type="submit" className="button secondary" disabled={disabled || length < MEMORY_CONTENT_MIN}><Plus size={16} /> 记下来</button></div>
  </form>
}

export function MemoryItems({ list, actorId, disabled, onRetract }: { list: MemoryList; actorId: string | null; disabled: boolean; onRetract: (id: string) => void }) {
  const items = profileMemories(list.items, actorId)
  if (!items.length) return <p className="muted small">还没有记下的偏好或目标。想好了再写，不写也完全可以。</p>
  return <ul className="memory-list">{items.map(m => <li key={m.id}>
    <div><span className="memory-category">{memoryCategoryLabels[m.category]}{!m.own && ' · 老师帮你记下'}</span><p>{m.content}</p>{m.valid_until && <span className="muted small">保留到 {dayLabel(m.valid_until)}</span>}</div>
    {m.own && <button className="button quiet" disabled={disabled} onClick={() => onRetract(m.id)} aria-label={`撤回：${m.content}`}><Undo2 size={15} /> 撤回</button>}
  </li>)}</ul>
}

export function PreferencesPage({ workspace, onBack }: { workspace: Workspace; onBack: () => void }) {
  const [list, setList] = useState<MemoryList | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const { client, studentId } = workspace
  const load = useCallback(async () => { try { setList(await client.memories()) } catch (e) { setError(safeError(e).message); workspace.report(e) } }, [client])
  useEffect(() => { setList(null); setError(''); void load() }, [load, studentId])
  const run = async (task: () => Promise<unknown>, done: string) => {
    if (busy) return false
    setBusy(true); setError(''); setNotice('')
    try { await task(); setNotice(done); return true }
    catch (e) { setError(safeError(e).message); workspace.report(e); return false }
    // The server does not deduplicate these writes, so always re-read instead of retrying blindly.
    finally { await load(); setBusy(false) }
  }
  const current = list?.student_id === studentId ? list : null
  return <section className="preferences-page">
    <button className="text-button" onClick={onBack}><ArrowLeft size={16} /> 回到作业本</button>
    <div className="page-title-row"><div><span className="eyebrow">我的设置</span><h1>我的偏好与目标</h1><p>告诉学伴你喜欢怎样的提示，或者写一个自己的小目标。只有你和老师能看到。</p></div></div>
    {error && <div className="inline-error" role="alert"><p>{error}</p><button className="text-button" disabled={busy} onClick={() => { setError(''); void load() }}><RefreshCw size={14} /> 重新读取</button></div>}
    {notice && <p className="message success" role="status">{notice}</p>}
    <div className="preferences-grid">
      <section className="preferences-card" aria-labelledby="memory-list-title"><h2 id="memory-list-title">学伴记下的</h2>{current ? <><MemoryItems list={current} actorId={workspace.identity?.actor_id ?? null} disabled={busy} onRetract={id => void run(() => client.retractMemory(id), '已经撤回，学伴不会再用这一条。')} /><p className="muted small">{current.notice}</p></> : !error && <p className="muted small" role="status"><LoaderCircle className="spin" size={14} /> 正在读取…</p>}</section>
      <section className="preferences-card" aria-labelledby="memory-form-title"><h2 id="memory-form-title">新写一条</h2><MemoryForm disabled={busy} onSubmit={(category, content) => run(() => client.addMemory(category, content), '记下来了。想改的时候，撤回再写一条就好。')} /></section>
    </div>
  </section>
}
