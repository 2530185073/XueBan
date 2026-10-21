import { useEffect } from 'react'
import { ArrowUpRight, Camera, Lightbulb, MessageCircle, ScanText, Sparkles, ImagePlus, RefreshCw } from 'lucide-react'
import type { CaseDetail } from '../core/types'
import { effectiveDecision } from '../core'
import type { Workspace } from '../useStudentWorkspace'
import { casePresentation, studentResultState } from '../presentation'
import { CaseRecordPanel } from './CaseRecord'
import { AgentTipsPanel } from './AgentTips'
import { RecognitionRecoveryPanel } from './RecognitionRecovery'

export function QuestionDetail({ row, workspace, onCorrection, onUpload, onContinue, embedded = false }: { row: CaseDetail; workspace: Workspace; onCorrection: () => void; onUpload?: () => void; onContinue?: () => void; embedded?: boolean }) {
  const presentation = casePresentation(row)
  const state = studentResultState(row)
  const canRetry = state === 'unprocessed' && row.attempt.recognition_status === 'ready' && row.case.check_ids.length === 0
  const decision = effectiveDecision(row)
  const needsConfirmation = state === 'processing' || row.attempt.recognition_status !== 'ready' || decision?.validity === 'needs_review' || decision?.verdict === 'unable_to_judge'
  const hint = workspace.hint
  const visibleHint = !needsConfirmation && hint?.validity === 'active' && hint.attempt_id === row.attempt.id && hint.source_decision_id === decision?.id && hint.source_decision_revision === decision?.revision ? hint : null
  useEffect(() => { if (visibleHint) workspace.hintViewed(visibleHint) }, [visibleHint?.id])
  const canHint = row.attempt.recognition_status === 'ready' && decision != null && !needsConfirmation
  return <section className="question-detail" aria-label="这道题的作答与提示">
    <div className={`result-callout ${presentation.tone}`} role="status"><span className="status-dot" /><div><strong>{presentation.label}</strong><p>{presentation.note}</p></div></div>
    {needsConfirmation && onContinue && <button className="text-button" onClick={onContinue}>先看其他题 <ArrowUpRight size={14} /></button>}
    <div className="transcript">{!embedded && <><div className="transcript-label"><ScanText size={16} /> 识别到的题目</div><p>{row.question.stem_text || '题干暂未读清，请结合左侧照片核对。'}</p><div className="answer-block"><span>这次作答</span><p>{row.attempt.answer_text || '暂未读到清楚的作答。'}</p></div></>}<button className="text-button subtle" disabled={!!workspace.busy} onClick={() => void workspace.requestHelp(row, 'recognition')}>文字和照片不一样？请老师核对 <ArrowUpRight size={14} /></button></div>
    <RecognitionRecoveryPanel key={`${row.case.id}:${row.case.revision}:${row.attempt.id}:${row.case.current_question_revision}:${row.attempt.recognition_revision}`} row={row} workspace={workspace} needed={needsConfirmation || state === 'service_failure' || state === 'unprocessed'} />
    <AgentTipsPanel caseId={row.case.id} workspace={workspace} />
    <div className="hint-section"><div className="section-label"><Lightbulb size={18} /><h3>卡住了，再给一点提示</h3></div><p className="muted small">{needsConfirmation ? '先核实题目和作答，再给解题提示，避免带偏你的思路。' : '先自己想一想，需要时再打开。'}</p><div className="hint-actions"><button className="button secondary" disabled={!!workspace.busy || !canHint} onClick={() => void workspace.requestHint(row, 'direction')}><Sparkles size={16} /> 给我一点方向</button><button className="button quiet" disabled={!!workspace.busy || !canHint} onClick={() => void workspace.requestHint(row, 'key_step')}>看看关键一步 <ArrowUpRight size={16} /></button></div>{visibleHint && !needsConfirmation && <div className="hint-note" aria-live="polite"><span className="eyebrow">给你的提示</span><p>{visibleHint.content}</p><small>AI 提示仍需结合题目核对。</small></div>}</div>
    {canRetry && <button className="button secondary" disabled={!!workspace.busy || !workspace.online} onClick={() => void workspace.retryQuestion(row)}><RefreshCw size={16} />重新检查这道题</button>}
    <button className="button primary correction-button" disabled={!!workspace.busy} onClick={onCorrection}><Camera size={18} /> 我已在纸上订正，拍新作答</button><p className="muted small correction-note">可以拍整页，也可以只拍这道题。保留题干和新作答，学伴会把这次订正接在原题记录后面。</p>
    {onUpload && <button className="button secondary" disabled={!!workspace.busy} onClick={onUpload}><ImagePlus size={16} />从相册选择订正照片</button>}
    <div className="teacher-help"><MessageCircle size={21} /><div><strong>需要老师一起看看？</strong><span>坐班老师就在身边。</span></div><button className="text-button" disabled={!!workspace.busy} onClick={() => void workspace.requestHelp(row, 'question')}>请老师帮忙 <ArrowUpRight size={16} /></button></div>
    <CaseRecordPanel row={row} workspace={workspace} />
  </section>
}
