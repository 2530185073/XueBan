import { useEffect, useMemo, useState } from 'react'
import { Bot, Check, Link2, Replace, UserCheck, X } from 'lucide-react'
import type { KnowledgeLink, KnowledgeLinkList, LinkDecision, LinkRole } from '../api/learningTypes'
import { primaryConflict, roleLabels, selectableNodes } from '../learning'
import { chapterNames, LoadNote, NodePicker, RefreshButton, useLoad, type LearningContext } from './LearningCommon'

type LinkChoice = Omit<LinkDecision, 'expected_question_revision' | 'expected_graph_version'>
export function withLinkVersions(snapshot: KnowledgeLinkList, choice: LinkChoice): LinkDecision {
  return { ...choice, expected_question_revision: snapshot.question_revision, expected_graph_version: snapshot.graph_version }
}
const decision = (link: Pick<KnowledgeLink, 'knowledge_id' | 'id'> | null, knowledgeId: string, role: LinkRole, status: LinkDecision['status'], rationale: string | null = null): LinkChoice =>
  ({ knowledge_id: knowledgeId, role, status, expected_link_id: link?.id ?? null, rationale })

export default function KnowledgeLinkEditor({ ctx, questionId, locked }: { ctx: LearningContext; questionId: string; locked?: string | null }) {
  const { learning, act, busy, version } = ctx
  const links = useLoad(questionId, signal => learning.links(questionId, signal), version)
  const graph = useLoad(`${questionId}:${links.data?.graph_version ?? ''}`, () => learning.graph(questionId), version)
  const nodes = useMemo(() => selectableNodes(graph.data?.nodes ?? []), [graph.data])
  const chapters = useMemo(() => chapterNames(graph.data?.nodes ?? []), [graph.data])
  const [adding, setAdding] = useState<string | null>(null)
  const [role, setRole] = useState<LinkRole>('primary')
  const [rationale, setRationale] = useState('')
  const [replacing, setReplacing] = useState<KnowledgeLink | null>(null)
  const [issue, setIssue] = useState<string | null>(null)
  useEffect(() => { setAdding(null); setReplacing(null); setIssue(null) }, [questionId, links.data?.question_revision, links.data?.graph_version])
  const items = links.data?.items ?? []
  const current = (knowledgeId: string) => items.find(link => link.knowledge_id === knowledgeId) ?? null
  const scopeMismatch = !!links.data && !!graph.data && links.data.graph_version !== graph.data.graph_version
  const disabled = busy || !!locked || links.loading || !!links.error || !links.data || graph.loading || !!graph.error || !graph.data || scopeMismatch
  const confirmed = items.filter(link => link.status === 'confirmed').sort((a, b) => Number(b.role === 'primary') - Number(a.role === 'primary'))
  const candidates = items.filter(link => link.status === 'candidate')
  const rejected = items.filter(link => link.status === 'rejected')

  async function decide(payload: LinkChoice, success: string) {
    if (disabled || !links.data) return
    const conflict = payload.status === 'confirmed' ? primaryConflict(items, payload.knowledge_id, payload.role) : null
    setIssue(conflict)
    if (conflict) return
    await act(() => learning.decideLink(questionId, withLinkVersions(links.data!, payload)), success)
  }
  async function add() {
    if (!adding || disabled || !links.data) return
    const snapshot = links.data
    const target = replacing
    const conflict = primaryConflict(items, adding, role, target?.knowledge_id ?? null)
    setIssue(conflict)
    if (conflict) return
    const done = await act(async () => {
      // Reject the old link first so the question never holds two primaries.
      if (target && target.knowledge_id !== adding) await learning.decideLink(questionId, withLinkVersions(snapshot, decision(target, target.knowledge_id, target.role, 'rejected', '替换为其他知识点')))
      return learning.decideLink(questionId, withLinkVersions(snapshot, decision(current(adding), adding, role, 'confirmed', rationale.trim() || null)))
    }, target ? '已替换知识点关联。' : '已确认知识点关联。')
    if (done) { setAdding(null); setRationale(''); setReplacing(null) }
  }

  return <section className="panel-section link-editor" aria-label="题目知识点">
    <div className="section-heading"><span><Link2 size={17} />题目知识点</span><RefreshButton state={links} label="刷新知识点关联" /></div>
    <p className="subtle">只有老师确认的关联才会让已核实的作答成为该知识点的证据。一道题最多一个主要知识点（定位错误的那一个），其余为次要。</p>
    {locked && <div className="preview-note">{locked}</div>}
    {scopeMismatch && <p className="warning-text" role="alert">题目使用的教材范围已更新。请刷新后重新选择知识点，当前选择不会提交。</p>}
    <LoadNote state={links}>
      {confirmed.length === 0 && <p className="subtle">还没有老师确认的知识点。</p>}
      <ul className="link-list">{confirmed.map(link => <li key={link.id} className="confirmed">
        <div className="help-row"><UserCheck size={13} /><strong>{link.knowledge_name ?? link.knowledge_id}</strong><span className={`tag ${link.role === 'primary' ? '' : 'muted'}`}>{roleLabels[link.role]}</span></div>
        {link.rationale && <small>说明：{link.rationale}</small>}
        <div className="button-row wrap">
          <button className="button" disabled={disabled} onClick={() => void decide(decision(link, link.knowledge_id, link.role === 'primary' ? 'secondary' : 'primary', 'confirmed', link.rationale), '已调整主次。')}>改为{link.role === 'primary' ? '次要' : '主要'}</button>
          <button className="button" disabled={disabled} onClick={() => { setReplacing(link); setRole(link.role); setAdding(null); setIssue(null) }}><Replace size={14} />替换为…</button>
          <button className="button" disabled={disabled} onClick={() => void decide(decision(link, link.knowledge_id, link.role, 'rejected'), '已驳回该关联。')}><X size={14} />驳回</button>
        </div>
      </li>)}</ul>
      {candidates.length > 0 && <>
        <p className="field-title">AI 候选（未经老师确认，不产生证据）</p>
        <ul className="link-list">{candidates.map(link => <li key={link.id} className="candidate">
          <div className="help-row"><Bot size={13} /><strong>{link.knowledge_name ?? link.knowledge_id}</strong><span className="tag amber">建议{link.role === 'primary' ? '主要' : '次要'}</span></div>
          {link.rationale && <small>AI 理由：{link.rationale}</small>}
          <div className="button-row wrap">
            <button className="button" disabled={disabled} onClick={() => void decide(decision(link, link.knowledge_id, 'primary', 'confirmed', link.rationale), '已确认为主要知识点。')}><Check size={14} />确认为主要</button>
            <button className="button" disabled={disabled} onClick={() => void decide(decision(link, link.knowledge_id, 'secondary', 'confirmed', link.rationale), '已确认为次要知识点。')}><Check size={14} />确认为次要</button>
            <button className="button" disabled={disabled} onClick={() => void decide(decision(link, link.knowledge_id, link.role, 'rejected'), '已驳回该候选。')}><X size={14} />驳回</button>
          </div>
        </li>)}</ul></>}
      {rejected.length > 0 && <details className="history"><summary>已驳回 {rejected.length} 条</summary>
        <ul className="link-list">{rejected.map(link => <li key={link.id}><span>{link.knowledge_name ?? link.knowledge_id}</span> <span className="tag muted">已驳回</span>{link.source === 'model' && <span className="tag muted">AI 候选</span>}</li>)}</ul></details>}

      <div className="tool-form link-add">
        <p className="field-title">{replacing ? `替换「${replacing.knowledge_name ?? replacing.knowledge_id}」为：` : '添加知识点'}</p>
        <LoadNote state={graph}>
          <NodePicker nodes={nodes} chapters={chapters} selected={adding ? [adding] : []} onToggle={id => setAdding(old => old === id ? null : id)} label="搜索具体知识点（不含章节）" disabled={disabled} max={1} />
        </LoadNote>
        <div className="form-grid">
          <label>主次<div className="select-wrap"><select value={role} disabled={disabled} onChange={event => setRole(event.target.value as LinkRole)}><option value="primary">主要（定位错误）</option><option value="secondary">次要</option></select></div></label>
          <label>说明（可选）<input value={rationale} maxLength={500} disabled={disabled} placeholder="例如：错在去括号时符号" onChange={event => setRationale(event.target.value)} /></label>
        </div>
        {issue && <small className="form-issue" role="alert">{issue}</small>}
        <div className="button-row">
          <button className="button primary" disabled={disabled || !adding} onClick={() => void add()}><Check size={14} />{replacing ? '确认替换' : '确认关联'}</button>
          {replacing && <button className="button" disabled={busy} onClick={() => { setReplacing(null); setAdding(null); setIssue(null) }}>取消替换</button>}
        </div>
      </div>
    </LoadNote>
  </section>
}
