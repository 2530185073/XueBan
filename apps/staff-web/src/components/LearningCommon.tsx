import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { ChevronDown, CircleHelp, LoaderCircle, RefreshCw, Search, X } from 'lucide-react'
import type { AuthorizedStudent } from '../api/contracts.generated'
import { errorMessage } from '../api/client'
import type { LearningApi } from '../api/learning'
import type { KnowledgeNode, LearningStatus } from '../api/learningTypes'
import { searchNodes, statusLabels, statusTone } from '../learning'

const PICKER_LIMIT = 30

export type Act = <T>(operation: () => Promise<T>, success: string | null) => Promise<T | null>

/** What every learning area needs from the shell: one session, one write lock, one refresh counter. */
export interface LearningContext {
  learning: LearningApi
  act: Act
  busy: boolean
  /** Bumped after every write or conflict so open panels re-read. */
  version: number
  students: AuthorizedStudent[]
  origin: string
}

export interface Loaded<T> { data: T | null; loading: boolean; error: string | null; reload: () => void }

/** Read on mount, when `key` changes (old data is dropped) and when `version` changes (old data stays until replaced). */
export function useLoad<T>(key: string | null, load: (signal: AbortSignal) => Promise<T>, version: string | number = 0): Loaded<T> {
  const [data, setData] = useState<T | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [tick, setTick] = useState(0)
  const shownKey = useRef<string | null>(null)
  const latest = useRef(load)
  latest.current = load
  useEffect(() => {
    if (key === null) { setData(null); setError(null); shownKey.current = null; return }
    if (shownKey.current !== key) { setData(null); shownKey.current = key }
    const controller = new AbortController()
    setLoading(true); setError(null)
    latest.current(controller.signal).then(value => { if (!controller.signal.aborted) setData(value) })
      .catch(cause => { if (!controller.signal.aborted) setError(errorMessage(cause)) })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [key, version, tick])
  return { data, loading, error, reload: () => setTick(n => n + 1) }
}

export function LoadNote({ state, empty, children }: { state: Loaded<unknown>; empty?: string; children?: ReactNode }) {
  if (state.error) return <p className="warning-text" role="alert"><CircleHelp size={13} /> {state.error} <button className="text-link" onClick={state.reload}>重试</button></p>
  if (state.loading && state.data === null) return <p className="subtle"><LoaderCircle size={13} className="spin" /> 正在读取…</p>
  if (state.data !== null && empty) return <p className="subtle">{empty}</p>
  return <>{children}</>
}

export function RefreshButton({ state, label }: { state: Loaded<unknown>; label: string }) {
  return <button className="icon-button" aria-label={label} title={label} disabled={state.loading} onClick={state.reload}><RefreshCw size={16} className={state.loading ? 'spin' : ''} /></button>
}

export function StatusTag({ status }: { status: LearningStatus }) {
  return <span className={`tag ${statusTone[status]}`}>{statusLabels[status]}</span>
}

export function StudentPicker({ students, value, onChange }: { students: AuthorizedStudent[]; value: string | null; onChange: (id: string) => void }) {
  if (students.length === 0) return <p className="subtle">当前账号还没有授权学员。</p>
  return <label className="student-picker">学员<div className="select-wrap"><select value={value ?? ''} onChange={event => onChange(event.target.value)}>
    {value === null && <option value="">请选择学员</option>}
    {students.map(student => <option key={student.student_id} value={student.student_id}>{student.display_name}</option>)}
  </select><ChevronDown size={14} /></div></label>
}

export function useGraph(learning: LearningApi) {
  return useLoad('graph', () => learning.graph())
}

/** Searchable list of concept/skill nodes; `multiple` keeps a chosen set (for 当前课次). */
export function NodePicker({ nodes, chapters, selected, onToggle, label, disabled, max }: {
  nodes: KnowledgeNode[]; chapters: Map<string, string>; selected: string[]; onToggle: (id: string) => void; label: string; disabled?: boolean; max?: number
}) {
  const [term, setTerm] = useState('')
  const found = useMemo(() => searchNodes(nodes, term), [nodes, term])
  const matches = found.slice(0, PICKER_LIMIT)
  const names = new Map(nodes.map(node => [node.id, node.name]))
  const full = max !== undefined && selected.length >= max
  return <div className="field node-picker">
    <label className="search-field"><span>{label}</span><span className="search-input"><Search size={14} /><input value={term} disabled={disabled} placeholder="按名称搜索知识点" onChange={event => setTerm(event.target.value)} /></span></label>
    {selected.length > 0 && <div className="chip-row" aria-label="已选知识点">{selected.map(id => <span key={id} className="chip">{names.get(id) ?? id}
      <button aria-label={`移除 ${names.get(id) ?? id}`} disabled={disabled} onClick={() => onToggle(id)}><X size={12} /></button></span>)}</div>}
    <ul className="node-options">{matches.map(node => {
      const on = selected.includes(node.id)
      return <li key={node.id}><button className={`node-option ${on ? 'selected' : ''}`} aria-pressed={on} disabled={disabled || (!on && full)} onClick={() => onToggle(node.id)}>
        <span>{node.name}</span><small>{node.parent_id ? chapters.get(node.parent_id) ?? '' : ''}</small></button></li>
    })}
      {matches.length === 0 && <li className="subtle">没有匹配的知识点。</li>}
      {found.length > matches.length && <li className="subtle">共 {found.length} 个，只显示前 {PICKER_LIMIT} 个；输入名称可找到其余知识点。</li>}</ul>
  </div>
}

export function chapterNames(nodes: KnowledgeNode[]): Map<string, string> {
  return new Map(nodes.filter(node => node.node_type === 'chapter').map(node => [node.id, node.name]))
}
