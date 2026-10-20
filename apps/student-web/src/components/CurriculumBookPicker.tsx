import { useEffect, useState } from 'react'
import { BookOpen, LoaderCircle, RefreshCw } from 'lucide-react'
import { safeError } from '../core/errors'
import type { CurriculumBooks } from '../core/learning'
import type { Workspace } from '../useStudentWorkspace'

/** Explicit book selection is separate from grade. Never silently opt into a new publisher. */
export function CurriculumBookPicker({ workspace, grade, onChanged }: { workspace: Workspace; grade: number | null | undefined; onChanged: () => void }) {
  const { client, studentId, recordVersion } = workspace
  const [read, setRead] = useState<{ key: string; value: CurriculumBooks } | null>(null)
  const [expanded, setExpanded] = useState(false)
  const [choice, setChoice] = useState(''), [error, setError] = useState(''), [loading, setLoading] = useState(false), [saving, setSaving] = useState(false), [tick, setTick] = useState(0)
  const key = [studentId, grade, recordVersion, tick].join('|')
  const data = read?.key === key ? read.value : null
  useEffect(() => {
    if (!studentId) return
    let live = true
    setLoading(true); setError('')
    client.curriculumBooks().then(value => { if (live) { setRead({ key, value }); setChoice(value.current_binding?.graph_version ?? ''); setLoading(false) } }, (cause: unknown) => { if (live) { setError(safeError(cause).message); setLoading(false); workspace.report(cause) } })
    return () => { live = false }
  }, [client, studentId, key])
  const current = data?.current_binding
  const selected = data?.books.find(book => book.graph_version === choice)
  const unavailable = !!current && !data?.books.some(book => book.graph_version === current.graph_version)
  async function save() {
    if (!data || saving || !studentId || (choice && !selected)) return
    const capturedStudent = studentId
    setSaving(true); setError('')
    try {
      const value = await client.selectCurriculumBook(choice || null, data.profile_revision)
      if (client.selectedStudentId !== capturedStudent) return
      setRead({ key, value }); setChoice(value.current_binding?.graph_version ?? ''); setExpanded(false); onChanged()
    } catch (cause) {
      if (client.selectedStudentId !== capturedStudent) return
      setError(`${safeError(cause).message} 请刷新教材选项，核对后重新选择。`); workspace.report(cause)
    } finally { setSaving(false) }
  }
  const disabled = loading || saving || !!workspace.busy || !data || !!error
  return <section className="knowledge-book-picker" aria-label="选择学校教材"><div className="knowledge-book-heading"><BookOpen size={17} /><div><h2>{current ? current.title : '和学校用同一本书'}</h2><p>{current ? `${current.publisher_name} · ${current.review_label}` : '确认出版社、年级和册次，知识地图才会对应自己的课堂。'}</p></div>{current && <button className="text-button" aria-expanded={expanded} disabled={saving} onClick={() => setExpanded(value => !value)}>{expanded ? '收起' : '更换教材'}</button>}</div>
    {loading ? <p className="knowledge-book-loading" role="status"><LoaderCircle className="spin" size={15} />正在读取教材选项…</p> : <>
      {data && (!current || expanded) && <><div className="knowledge-book-controls"><div className="knowledge-book-field"><label htmlFor="knowledge-book-select">当前教材</label><select id="knowledge-book-select" value={choice} disabled={disabled} onChange={event => setChoice(event.target.value)}><option value="">默认教材范围（未指定一本书）</option>{unavailable && <option value={current!.graph_version} disabled>{current!.title} · 暂不可用</option>}{data.books.map(book => <option value={book.graph_version} key={book.graph_version}>{book.title} · {book.publisher_name}</option>)}</select></div><button className="button secondary" disabled={disabled || choice === (current?.graph_version ?? '') || (!!choice && !selected)} onClick={() => void save()}>{saving ? <LoaderCircle className="spin" size={15} /> : null}保存教材</button></div>
      {(selected || current) && <div className="knowledge-book-meta"><span>{(selected ?? current)!.review_label}</span><span>{(selected ?? current)!.isbn ? `ISBN ${(selected ?? current)!.isbn}` : '书号待补充'}</span></div>}
      {(selected || current)?.source_note && <p className="knowledge-book-note">{(selected ?? current)!.source_note}</p>}
      {unavailable && <p className="knowledge-book-note">当前选择已保留，但该书图谱暂不可用；可以继续拍照检查。</p>}
      {!data.books.length && <p className="knowledge-book-note">当前年级暂没有可选择的具体书目。拍照检查可以继续，知识地图等目录就绪后再补全。</p>}</>}
      {error && <p className="knowledge-book-error" role="alert">{error} <button className="text-button" disabled={saving} onClick={() => { setTick(n => n + 1); onChanged() }}><RefreshCw size={14} />刷新教材选项</button></p>}
    </>}
  </section>
}
