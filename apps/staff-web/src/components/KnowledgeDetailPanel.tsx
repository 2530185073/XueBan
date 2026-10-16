import { useState } from 'react'
import { Check, ClipboardList, Stethoscope, Undo2 } from 'lucide-react'
import type { Diagnosis, Evidence } from '../api/learningTypes'
import {
  assistanceLabels, dateTime, diagnosisIssue, eventLabels, invalidationLabels, openQuestionLabels, orderEvidence, reasonLabels, resultLabels, roleLabels,
  sufficiencyLabels,
} from '../learning'
import { LoadNote, RefreshButton, StatusTag, useLoad, type LearningContext } from './LearningCommon'

export function EvidenceRow({ item }: { item: Evidence }) {
  const valid = item.validity === 'valid'
  return <div className={`evidence-row ${valid ? '' : 'invalid'}`}>
    <div className="help-row"><span className={`tag ${item.result === 'correct' ? '' : item.result === 'unscorable' ? 'muted' : 'rose'}`}>{resultLabels[item.result] ?? item.result}</span>
      <span className="tag muted">{eventLabels[item.event_type] ?? item.event_type}</span>{item.independent && <span className="tag">独立完成</span>}
      {!valid && <span className="tag muted">已失效</span>}<time>{dateTime(item.occurred_at)}</time></div>
    <p>{item.question_text ?? '（题干待识别）'}</p>
    <small>{item.homework_title ?? '未命名作业'} · {assistanceLabels[item.assistance_level] ?? item.assistance_level} · {roleLabels[item.link_role]}
      {!valid && ` · ${invalidationLabels[item.invalidation_reason ?? ''] ?? '已失效'}`}</small>
  </div>
}

const kindLabels: Record<Diagnosis['kind'], string> = { suspected_cause: '可能原因（未核实）', verified_weak_scope: '已核实薄弱范围' }

export default function KnowledgeDetailPanel({ ctx, studentId, knowledgeId, pending }: { ctx: LearningContext; studentId: string; knowledgeId: string; pending: boolean }) {
  const detail = useLoad(`${studentId}/${knowledgeId}`, signal => ctx.learning.knowledgeDetail(studentId, knowledgeId, signal), ctx.version)
  const [kind, setKind] = useState<Diagnosis['kind']>('suspected_cause')
  const [scope, setScope] = useState('')
  const [picked, setPicked] = useState<string[]>([])
  const data = detail.data
  const evidence = orderEvidence(data?.evidence ?? [])
  const valid = evidence.filter(item => item.validity === 'valid')
  const state = data?.state ?? null
  const chosen = picked.filter(id => valid.some(item => item.id === id))
  const issue = diagnosisIssue(kind, scope, chosen)
  const toggle = (id: string) => setPicked(old => old.includes(id) ? old.filter(x => x !== id) : [...old, id])

  async function add() {
    if (issue) return
    const made = await ctx.act(() => ctx.learning.addDiagnosis(studentId, knowledgeId, { kind, scope_text: scope.trim(), evidence_ids: chosen }), `已记录${kindLabels[kind]}。学员画像汇总后显示。`)
    if (made) { setScope(''); setPicked([]) }
  }

  return <div className="card detail-card">
    <div className="section-heading"><span><ClipboardList size={17} />{data?.node.name ?? '知识点'}</span><RefreshButton state={detail} label="刷新知识点详情" /></div>
    <LoadNote state={detail}>{data && <>
      {data.node.observable_objective && <p className="subtle">可观察目标：{data.node.observable_objective}</p>}
      {state ? <div className="state-summary">
        <div className="help-row"><StatusTag status={state.learning_status} /><span className="tag muted">{sufficiencyLabels[state.evidence_sufficiency] ?? state.evidence_sufficiency}</span></div>
        <ul className="plain-list">{state.reason_codes.map(code => <li key={code}>{reasonLabels[code] ?? code}</li>)}</ul>
        <p className="subtle">{state.sufficiency_explanation} · 已核实作答 {state.scorable_evidence_count} 次，其中独立完成 {state.independent_evidence_count} 次、出错 {state.observed_error_count} 次</p>
        {state.open_questions.length > 0 && <p className="warning-text">还不知道：{state.open_questions.map(q => `${openQuestionLabels[q.code] ?? q.code}（${q.count}）`).join('；')}</p>}
      </div> : <p className="subtle">暂无状态：还没有经老师核实并关联到此知识点的作答。</p>}
      <p className="subtle">{data.notice}</p>
      {(data.prerequisites.length > 0 || data.leads_to.length > 0) && <div className="related">
        {data.prerequisites.length > 0 && <p><span className="field-title">前置：</span>{data.prerequisites.map(node => <span key={node.id} className="related-node">{node.name} <StatusTag status={node.learning_status} /></span>)}</p>}
        {data.leads_to.length > 0 && <p><span className="field-title">后续：</span>{data.leads_to.map(node => <span key={node.id} className="related-node">{node.name} <StatusTag status={node.learning_status} /></span>)}</p>}
      </div>}

      <h3 className="block-title">证据（{valid.length} 条有效{evidence.length > valid.length ? `，${evidence.length - valid.length} 条已失效` : ''}）</h3>
      {evidence.length === 0 ? <p className="subtle">还没有证据。</p> : <div className="evidence-list">{evidence.map(item => <EvidenceRow key={item.id} item={item} />)}</div>}

      <h3 className="block-title"><Stethoscope size={15} />诊断</h3>
      {pending && <p className="preview-note">正在汇总最新改判；刚记录或撤回的诊断稍后显示。</p>}
      {(state?.diagnoses ?? []).length === 0 ? <p className="subtle">还没有诊断。</p> : <ul className="hint-list">{state!.diagnoses.map(item => <li key={item.id}>
        <div className="help-row"><span className={`tag ${item.kind === 'verified_weak_scope' ? '' : 'amber'}`}>{kindLabels[item.kind]}</span><small>引用 {item.evidence_ids.length} 条证据</small></div>
        <p>{item.scope_text}</p>
        <button className="button" disabled={ctx.busy} onClick={() => void ctx.act(() => ctx.learning.retractDiagnosis(item.id), '已撤回该诊断。')}><Undo2 size={14} />撤回</button>
      </li>)}</ul>}
      <div className="tool-form">
        <p className="field-title">记录诊断</p>
        <label>类型<div className="select-wrap"><select value={kind} disabled={ctx.busy} onChange={event => setKind(event.target.value as Diagnosis['kind'])}>
          <option value="suspected_cause">可能原因（未核实，报告中标为“尚未核实”）</option><option value="verified_weak_scope">已核实薄弱范围（至少两条证据）</option></select></div></label>
        <label>{kind === 'verified_weak_scope' ? '具体子范围' : '可能原因'}<textarea value={scope} maxLength={500} disabled={ctx.busy} placeholder={kind === 'verified_weak_scope' ? '例如：异号两数相加时取符号' : '例如：可能把减法当成加法处理符号'} onChange={event => setScope(event.target.value)} /></label>
        <fieldset className="choice-list" disabled={ctx.busy}><legend>引用证据（只能选当前有效的）</legend>
          {valid.length === 0 && <p className="subtle">没有有效证据可引用。</p>}
          {valid.map(item => <label key={item.id} className="check"><input type="checkbox" checked={chosen.includes(item.id)} onChange={() => toggle(item.id)} />
            <span>{dateTime(item.occurred_at)} · {resultLabels[item.result] ?? item.result} · {item.question_text ?? '（题干待识别）'}</span></label>)}
        </fieldset>
        {scope && issue && <small className="form-issue">{issue}</small>}
        <button className="button primary" disabled={ctx.busy || !!issue} onClick={() => void add()}><Check size={14} />记录诊断</button>
      </div>
    </>}</LoadNote>
  </div>
}
