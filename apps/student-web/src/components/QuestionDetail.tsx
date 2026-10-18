import { useEffect } from 'react'
import { ArrowUpRight, Camera, Lightbulb, MessageCircle, ScanText, Sparkles } from 'lucide-react'
import type { CaseDetail } from '../core/types'
import { effectiveDecision } from '../core'
import type { Workspace } from '../useStudentWorkspace'
import { casePresentation } from '../presentation'
import { CaseRecordPanel } from './CaseRecord'
import { AgentTipsPanel } from './AgentTips'

export function QuestionDetail({ row, workspace, onCorrection, onContinue }: { row: CaseDetail; workspace: Workspace; onCorrection: () => void; onContinue?: () => void }) {
  const presentation = casePresentation(row)
  const decision = effectiveDecision(row)
  const needsConfirmation = row.attempt.recognition_status !== 'ready' || decision?.validity === 'needs_review' || decision?.verdict === 'unable_to_judge'
  const hint = workspace.hint
  const visibleHint = !needsConfirmation && hint?.validity === 'active' && hint.attempt_id === row.attempt.id && hint.source_decision_id === decision?.id && hint.source_decision_revision === decision?.revision ? hint : null
  useEffect(() => { if (visibleHint) workspace.hintViewed(visibleHint) }, [visibleHint?.id])
  const canHint = row.attempt.recognition_status === 'ready' && decision != null && !needsConfirmation
  return <section className="question-detail" aria-label="这道题的作答与提示">
    <div className={`result-callout ${presentation.tone}`} role="status"><span className="status-dot" /><div><strong>{presentation.label}</strong><p>{presentation.note}</p></div></div>
    {needsConfirmation && onContinue && <button className="text-button" onClick={onContinue}>先看其他题 <ArrowUpRight size={14} /></button>}
    <div className="transcript"><div className="transcript-label"><ScanText size={16} /> 识别到的题目</div><p>{row.question.stem_text || '题干暂未读清，请结合左侧照片核对。'}</p><div className="answer-block"><span>这次作答</span><p>{row.attempt.answer_text || '暂未读到清楚的作答。'}</p></div><button className="text-button subtle" disabled={!!workspace.busy} onClick={() => void workspace.requestHelp(row, 'recognition')}>文字和照片不一样 <ArrowUpRight size={14} /></button></div>
    <AgentTipsPanel caseId={row.case.id} workspace={workspace} />
    <div className="hint-section"><div className="section-label"><Lightbulb size={18} /><h3>卡住了，再给一点提示</h3></div><p className="muted small">{needsConfirmation ? '先核实题目和作答，再给解题提示，避免带偏你的思路。' : '先自己想一想，需要时再打开。'}</p><div className="hint-actions"><button className="button secondary" disabled={!!workspace.busy || !canHint} onClick={() => void workspace.requestHint(row, 'direction')}><Sparkles size={16} /> 给我一点方向</button><button className="button quiet" disabled={!!workspace.busy || !canHint} onClick={() => void workspace.requestHint(row, 'key_step')}>看看关键一步 <ArrowUpRight size={16} /></button></div>{visibleHint && !needsConfirmation && <div className="hint-note" aria-live="polite"><span className="eyebrow">给你的提示</span><p>{visibleHint.content}</p><small>AI 提示仍需结合题目核对。</small></div>}</div>
    <button className="button primary correction-button" disabled={!!workspace.busy} onClick={onCorrection}><Camera size={18} /> 我已在纸上订正，拍新作答</button><p className="muted small correction-note">把同一道题的题干和新作答一起拍清楚。原来的作答会保留。</p>
    <div className="teacher-help"><MessageCircle size={21} /><div><strong>需要老师一起看看？</strong><span>坐班老师就在身边。</span></div><button className="text-button" disabled={!!workspace.busy} onClick={() => void workspace.requestHelp(row, 'question')}>请老师帮忙 <ArrowUpRight size={16} /></button></div>
    <CaseRecordPanel row={row} workspace={workspace} />
  </section>
}
