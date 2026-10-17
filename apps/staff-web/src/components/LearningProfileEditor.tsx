import { useEffect, useMemo, useState } from 'react'
import { BookMarked, Check } from 'lucide-react'
import type { LearningProfile } from '../api/learningTypes'
import { dateTime, selectableNodes } from '../learning'
import { PROFILE_GRADES, TEXTBOOK_REVISIONS, textbookPlanText } from '../textbookPlan'
import { chapterNames, LoadNote, NodePicker, useGraph, useLoad, type LearningContext } from './LearningCommon'

interface Draft { grade: string; term: string; textbook: string; current: string[]; note: string }
const draftOf = (profile: LearningProfile): Draft => ({
  grade: profile.grade?.toString() ?? '', term: profile.term ?? '', textbook: profile.textbook_revision ?? '', current: profile.current_knowledge_ids, note: profile.note ?? '',
})

export default function LearningProfileEditor({ ctx, studentId }: { ctx: LearningContext; studentId: string }) {
  const profile = useLoad(studentId, signal => ctx.learning.profile(studentId, signal), ctx.version)
  const graph = useGraph(ctx.learning)
  const nodes = useMemo(() => selectableNodes(graph.data?.nodes ?? []), [graph.data])
  const chapters = useMemo(() => chapterNames(graph.data?.nodes ?? []), [graph.data])
  const [draft, setDraft] = useState<Draft | null>(null)
  const data = profile.data
  // A new server revision (own save or a colleague's) replaces the form; unrelated reloads keep unsaved edits.
  useEffect(() => { setDraft(data ? draftOf(data) : null) }, [data?.student_id, data?.revision])
  const set = (change: Partial<Draft>) => setDraft(old => old ? { ...old, ...change } : old)

  async function save() {
    if (!data || !draft) return
    await ctx.act(() => ctx.learning.saveProfile(studentId, {
      expected_revision: data.revision, grade: draft.grade ? Number(draft.grade) : null, term: (draft.term || null) as LearningProfile['term'],
      textbook_revision: draft.textbook.trim() || null, current_knowledge_ids: draft.current, note: draft.note.trim() || null,
    }), '学员档案已保存。')
  }

  return <details className="card profile-card">
    <summary className="section-heading"><span><BookMarked size={17} />学习档案 · 下一课 / 当前课次</span><small>{data?.updated_at ? `更新于 ${dateTime(data.updated_at)}` : '尚未填写'}</small></summary>
    <LoadNote state={profile}>{draft && <div className="tool-form">
      <p className="subtle">“当前课次”用于预习和报告里的“还不知道的”。只填学校正在或即将讲的内容，不代表孩子需要补课。</p>
      <div className="form-grid">
        <label>年级<div className="select-wrap"><select value={draft.grade} disabled={ctx.busy} onChange={event => set({ grade: event.target.value })}>
          <option value="">未设置</option>{PROFILE_GRADES.map(([grade, label]) => <option key={grade} value={grade}>{label}</option>)}
          {draft.grade && !PROFILE_GRADES.some(([grade]) => String(grade) === draft.grade) && <option value={draft.grade}>{draft.grade} 年级</option>}</select></div></label>
        <label>学期<div className="select-wrap"><select value={draft.term} disabled={ctx.busy} onChange={event => set({ term: event.target.value })}>
          <option value="">未设置</option><option value="upper">上学期</option><option value="lower">下学期</option></select></div></label>
        <label>教材版本（可选）<input value={draft.textbook} maxLength={40} disabled={ctx.busy} list="profile-textbook-revisions" placeholder="例如：2024 新版" onChange={event => set({ textbook: event.target.value })} />
          <datalist id="profile-textbook-revisions">{TEXTBOOK_REVISIONS.map(value => <option key={value} value={value} />)}</datalist></label>
      </div>
      {draft.grade && <p className="subtle">本校选用教材：{textbookPlanText(Number(draft.grade))}</p>}
      <LoadNote state={graph}><NodePicker nodes={nodes} chapters={chapters} selected={draft.current} max={20} disabled={ctx.busy} label="当前课次知识点（最多 20 个）"
        onToggle={id => set({ current: draft.current.includes(id) ? draft.current.filter(x => x !== id) : [...draft.current, id] })} /></LoadNote>
      <label>备注（可选）<textarea value={draft.note} maxLength={500} disabled={ctx.busy} onChange={event => set({ note: event.target.value })} /></label>
      <button className="button primary" disabled={ctx.busy} onClick={() => void save()}><Check size={14} />保存档案</button>
    </div>}</LoadNote>
  </details>
}
