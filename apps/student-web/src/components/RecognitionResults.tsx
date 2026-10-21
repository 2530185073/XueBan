import { useEffect, useState } from 'react'
import { Camera, Check, ChevronDown, CircleAlert, CircleX, Clock3, Lightbulb, LoaderCircle, Shuffle } from 'lucide-react'
import type { CaseDetail } from '../core/types'
import type { CurriculumScope } from '../core/learning'
import type { Workspace } from '../useStudentWorkspace'
import { effectiveDecision } from '../core'
import { casePresentation, studentQuestionText, studentResultState, studentResultUncertain, studentUnresolvedKind, type StudentResultState } from '../presentation'
import { ResultsSummary } from './ResultsSummary'
import { QuestionDetail } from './QuestionDetail'

interface RecognitionResultsProps {
  rows: CaseDetail[]
  workspace: Workspace
  onNext: () => void
  selectedCaseId: string | null
  onSelect: (row: CaseDetail) => void
  onCorrection: (row: CaseDetail, camera: boolean) => void
  onSelectSubject: (row: CaseDetail, subject: string) => void
  onVariant?: (row: CaseDetail) => void
  variantBusy?: string
}

const stateLabels: Record<StudentResultState, string> = {
  correct: 'AI 初判正确',
  incorrect: '需要订正',
  uncertain: '待确认',
  processing: '处理中',
  unprocessed: '尚未检查',
  service_failure: '服务失败',
}

const slotFeedback = {
  correct: { tone: 'correct', label: '✓ 正确' },
  incorrect: { tone: 'incorrect', label: '✕ 错误' },
  unanswered: { tone: 'unanswered', label: '待完成' },
  unable_to_judge: { tone: 'uncertain', label: '待核对' },
} as const

const controlledServiceUncertaintyMessages = new Set([
  '请先在学生档案设置已支持的年级，并选择本题学科。',
  '本题不在已配置的首发年级、学科和教材出版社范围内，暂不提供判题结论。',
  '模型复核仍未能确认本题对错，请核对题目和作答后重试。',
  '首轮与独立复核的结论不一致，请核对原题后重试。',
])

function answerText(row: CaseDetail) {
  if (row.attempt.answer_status === 'blank') return '未填写作答'
  return row.attempt.answer_text || '暂未读到清楚的作答'
}

function ResultIcon({ state }: { state: StudentResultState }) {
  if (state === 'correct') return <Check size={17} strokeWidth={2.5} />
  if (state === 'incorrect') return <CircleX size={17} strokeWidth={2.2} />
  if (state === 'processing') return <LoaderCircle className="spin" size={17} strokeWidth={2.2} />
  if (state === 'unprocessed') return <Clock3 size={17} strokeWidth={2.2} />
  if (state === 'service_failure') return <CircleX size={17} strokeWidth={2.2} />
  return <CircleAlert size={17} strokeWidth={2.2} />
}

export function QuestionCard({ row, index, open, onToggle, subjects = [], onSelectSubject = () => {}, workspace, onCorrection, onContinue, onVariant, variantBusy = false }: { row: CaseDetail; index: number; open: boolean; onToggle: () => void; subjects?: CurriculumScope[]; onSelectSubject?: (subject: string) => void; workspace?: Workspace; onCorrection?: (camera: boolean) => void; onContinue?: () => void; onVariant?: () => void; variantBusy?: boolean }) {
  const state = studentResultState(row)
  const serviceIncomplete = studentUnresolvedKind(row) === 'service'
  const decision = effectiveDecision(row)
  const usable = state !== 'processing' && decision && decision.validity !== 'needs_review' && decision.verdict !== 'unable_to_judge'
  const firstUncertainty = decision?.uncertainties?.[0]
  const controlledServiceMessage = firstUncertainty && controlledServiceUncertaintyMessages.has(firstUncertainty) ? firstUncertainty : undefined
  const uncertain = studentResultUncertain(row)
  const method = decision?.correction_method?.trim()
  const points = decision?.knowledge_points ?? []
  const stemText = studentQuestionText(row.question.stem_text, row.question.label)
  const uncertainties = [...new Set([
    ...(row.question.recognition_uncertainties ?? []),
    ...(row.attempt.recognition_uncertainties ?? []),
    ...(decision?.uncertainties ?? []),
  ])]
  return <article className={`recognition-card ${state} ${open ? 'open' : ''}`}>
    <button className="recognition-card-head" type="button" aria-expanded={open} onClick={onToggle}>
      <span className="recognition-index">{index + 1}</span>
      <span className="recognition-card-title">{stemText || '题干待核实'}</span>
      <span className={`recognition-status ${state}`}>{uncertain && <sup className="result-star" aria-label="AI识别不确定">*</sup>}<ResultIcon state={state} />{state === 'correct' && decision?.origin === 'staff' && decision.validity === 'confirmed' ? '老师确认正确' : state === 'uncertain' ? casePresentation(row).label : stateLabels[state]}</span>
      <ChevronDown className="recognition-chevron" size={19} aria-hidden="true" />
    </button>
    {open && <div className="recognition-card-body">
      <div className="recognition-question-block">
        <span className="recognition-label">题目</span>
        <p>{stemText || '题干暂未读清。'}</p>
        {!!row.question.blank_count && <span className="blank-note">填空题 · {row.question.blank_count} 个空</span>}
      </div>
      {usable && decision.answer_checks?.length ? <div className="recognition-answer-slots" aria-label="逐空判定">
        {decision.answer_checks.map(part => <div key={part.index} className={`recognition-answer ${slotFeedback[part.verdict].tone}`}>
          <span className="recognition-label">第{part.index}{row.question.blank_count ? '空' : '项'} · {part.prompt}</span>
          <p>{part.student_answer || (part.verdict === 'unanswered' ? '未作答' : '暂未读到清楚的作答')} <span className="slot-verdict">{slotFeedback[part.verdict].label}</span></p>
          {part.verdict === 'incorrect' && part.correct_answer && <p className="slot-correction">应填：{part.correct_answer}</p>}
        </div>)}
      </div> : <div className={`recognition-answer ${state}`}>
        <span className="recognition-label">{serviceIncomplete ? 'AI 识读草稿' : '我的作答'}</span>
        <p>{serviceIncomplete ? row.attempt.answer_text || 'AI 暂未读到作答，仍需结合原图核对。' : answerText(row)}</p>
        {serviceIncomplete && <small>这段文字尚未完成核对，可能与照片不同，请以原图为准。</small>}
      </div>}
      {state === 'incorrect' && <div className="recognition-correct-answer">
        <span className="recognition-label">正确答案</span>
        <p>{decision?.correct_answer?.trim() || '暂未生成正确答案，请重新识别或请老师核对。'}</p>
      </div>}
      <div className="recognition-learning">
        {!row.question.subject && !serviceIncomplete && subjects.length > 0 && <div className="recognition-subject-picker">
          <span className="recognition-label">无法确定学科</span>
          <div><select aria-label="选择题目学科" disabled={!!workspace?.busy} defaultValue="" onChange={event => { if (event.target.value) onSelectSubject(event.target.value) }}>
            <option value="">请选择本题学科</option>
            {subjects.map(subject => <option key={subject.subject} value={subject.subject}>{subject.subject} · {subject.publisher_name}</option>)}
          </select><small>仅显示当前年级已配置且官方目录匹配的最新版教材。</small></div>
        </div>}
        <div className="recognition-learning-row"><span>知识点</span><div className="knowledge-tags">{points.length ? points.map(point => <span key={point}>{point}</span>) : <em>暂未识别</em>}</div></div>
        {method && usable && decision && ['incorrect', 'partially_correct', 'unanswered'].includes(decision.verdict) && <div className="recognition-learning-row correction-method"><span><Lightbulb size={15} />改正方法</span><p>{method}</p></div>}
      </div>
      {uncertainties.length > 0 && <p className="recognition-uncertainty">{state === 'uncertain' ? casePresentation(row).note : state === 'processing' ? '正在检查这道题的作答，可以先继续其他题。' : controlledServiceMessage ?? '* AI 对这道题的识别或判断存在不确定，请结合原图核对。'}</p>}
      {workspace && onCorrection && <QuestionDetail row={row} workspace={workspace} embedded onCorrection={() => onCorrection(true)} onUpload={() => onCorrection(false)} onContinue={onContinue} />}
      {onVariant && usable && decision && ['incorrect', 'partially_correct'].includes(decision.verdict) && <div className="recognition-variant-action"><button type="button" className="button secondary" disabled={variantBusy || !!workspace?.busy} onClick={onVariant}>{variantBusy ? <LoaderCircle className="spin" size={16} /> : <Shuffle size={16} />}{variantBusy ? '正在提交生成请求' : '选做一道同题型'}</button><span className="muted small">AI 生成并核验一道，想试试时再开始。</span></div>}
    </div>}
  </article>
}

export function RecognitionResults({ rows, workspace, onNext, onSelectSubject, selectedCaseId, onSelect, onCorrection, onVariant, variantBusy }: RecognitionResultsProps) {
  const [openId, setOpenId] = useState<string | null>(selectedCaseId)
  useEffect(() => { setOpenId(selectedCaseId) }, [selectedCaseId])
  const subjects = workspace.studentCurriculum?.subjects.filter(subject => subject.catalog_status === 'available') ?? []
  const select = (row: CaseDetail) => { setOpenId(row.case.id); onSelect(row) }
  return <div className="recognition-results">
    <ResultsSummary rows={rows} detail={null} compact />
    <p className="recognition-legend">点击题目，对照原图核对作答。AI 初判带 * 时仍有不确定之处；你可以先订正清楚的题，原作答会保留。</p>
    <div className="recognition-card-list" aria-label="本次识别的题目">{rows.map((row, index) => <QuestionCard key={row.case.id} row={row} index={index} workspace={workspace} subjects={subjects} onVariant={onVariant ? () => onVariant(row) : undefined} variantBusy={!!variantBusy} onSelectSubject={subject => onSelectSubject(row, subject)} open={openId === row.case.id} onToggle={() => { onSelect(row); setOpenId(openId === row.case.id ? null : row.case.id) }} onCorrection={camera => onCorrection(row, camera)} onContinue={rows.length > 1 ? () => select(rows[(index + 1) % rows.length]) : undefined} />)}</div>
    <div className="next-capture-wrap"><button className="button primary next-capture" type="button" disabled={!!workspace.busy} onClick={onNext}><Camera size={18} />拍下一份作业</button></div>
  </div>
}
