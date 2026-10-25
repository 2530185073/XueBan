import { useState } from 'react'
import { Camera, Check, ChevronDown, Circle, CircleAlert, CircleX, Clock3, Lightbulb, LoaderCircle } from 'lucide-react'
import type { CaseDetail } from '../core/types'
import type { CurriculumScope } from '../core/learning'
import type { Workspace } from '../useStudentWorkspace'
import { effectiveDecision } from '../core'
import { studentQuestionText, studentResultState, studentResultUncertain, type StudentResultState } from '../presentation'
import { ResultsSummary } from './ResultsSummary'

interface RecognitionResultsProps {
  rows: CaseDetail[]
  workspace: Workspace
  onNext: () => void
  onSelectSubject: (row: CaseDetail, subject: string) => void
  subjects?: CurriculumScope[]
  onSelect?: (row: CaseDetail) => void
  // Accepted for existing callers; the simple result view owns its expansion
  // and does not expose correction, hint, help, or practice workflows.
  selectedCaseId?: string | null
  onCorrection?: (row: CaseDetail, camera: boolean) => void
  onContinue?: () => void
  onVariant?: (row: CaseDetail) => void
  variantBusy?: string
}

interface QuestionCardProps {
  row: CaseDetail
  index: number
  open: boolean
  onToggle: () => void
  subjects?: CurriculumScope[]
  onSelectSubject?: (subject: string) => void
  workspace?: Workspace
  onCorrection?: (camera: boolean) => void
  onContinue?: () => void
  onVariant?: () => void
  variantBusy?: boolean
}

const stateLabels: Record<StudentResultState, string> = {
  correct: '正确',
  incorrect: '错误',
  unanswered: '未作答',
  uncertain: '待确认',
  processing: '处理中',
  unprocessed: '尚未检查',
  service_failure: '服务失败',
}

const slotFeedback = {
  correct: { tone: 'correct', label: '✓ 正确' },
  incorrect: { tone: 'incorrect', label: '✕ 错误' },
  unanswered: { tone: 'unanswered', label: '未作答' },
  unable_to_judge: { tone: 'uncertain', label: '待核对' },
} as const

const controlledServiceUncertaintyMessages = new Set([
  '请先在学生档案设置已支持的年级，并选择本题学科。',
  '本题不在已配置的首发年级、学科和教材出版社范围内，暂不提供判题结论。',
  '模型复核仍未能确认本题对错，请核对题目和作答后重试。',
  '首轮与独立复核的结论不一致，请核对原题后重试。',
])

function answerText(row: CaseDetail) {
  if (row.attempt.answer_status === 'blank' || studentResultState(row) === 'unanswered') return '未作答'
  return row.attempt.answer_text || '暂未读到清楚的作答'
}

function ResultIcon({ state }: { state: StudentResultState }) {
  if (state === 'correct') return <Check size={17} strokeWidth={2.5} />
  if (state === 'incorrect') return <CircleX size={17} strokeWidth={2.2} />
  if (state === 'unanswered') return <Circle size={17} strokeWidth={2.2} />
  if (state === 'processing') return <LoaderCircle className="spin" size={17} strokeWidth={2.2} />
  if (state === 'unprocessed') return <Clock3 size={17} strokeWidth={2.2} />
  if (state === 'service_failure') return <CircleX size={17} strokeWidth={2.2} />
  return <CircleAlert size={17} strokeWidth={2.2} />
}

export function QuestionCard({ row, index, open, onToggle, subjects = [], onSelectSubject = () => {}, workspace }: QuestionCardProps) {
  const state = studentResultState(row)
  const decision = effectiveDecision(row)
  const usable = (state === 'correct' || state === 'incorrect') && decision && decision.validity !== 'needs_review' && decision.verdict !== 'unable_to_judge'
  const firstUncertainty = decision?.uncertainties?.[0]
  const controlledServiceMessage = firstUncertainty && controlledServiceUncertaintyMessages.has(firstUncertainty) ? firstUncertainty : undefined
  const uncertain = studentResultUncertain(row)
  const solutionSteps = decision?.correction_method
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
      <span className={`recognition-status ${state}`}>{uncertain && <sup className="result-star" aria-label="AI识别不确定">*</sup>}<ResultIcon state={state} />{stateLabels[state]}</span>
      <ChevronDown className="recognition-chevron" size={19} aria-hidden="true" />
    </button>
    {open && <div className="recognition-card-body">
      <div className="recognition-question-block">
        <span className="recognition-label">题目</span>
        <p>{stemText || '题干暂未读清。'}</p>
        {!!row.question.blank_count && <span className="blank-note">填空题 · {row.question.blank_count} 个空</span>}
      </div>
      {!!row.question.options?.length && <ol className="recognition-original-options" aria-label="原题选项">{row.question.options.map((option, optionIndex) => <li key={`${optionIndex}:${option.label}`}><strong>{option.label}</strong><span>{option.text}</span></li>)}</ol>}
      {usable && decision.answer_checks?.length ? <div className="recognition-answer-slots" aria-label="逐空判定">
        {decision.answer_checks.map(part => <div key={part.index} className={`recognition-answer ${slotFeedback[part.verdict].tone}`}>
          <span className="recognition-label">第{part.index}{row.question.blank_count ? '空' : '项'} · {part.prompt}</span>
          <p>{part.student_answer || (part.verdict === 'unanswered' ? '未作答' : '暂未读到清楚的作答')} <span className="slot-verdict">{slotFeedback[part.verdict].label}</span></p>
          {part.verdict === 'incorrect' && part.correct_answer && <p className="slot-correction">应填：{part.correct_answer}</p>}
        </div>)}
      </div> : <div className={`recognition-answer ${state}`}>
        <span className="recognition-label">我的作答</span>
        <p>{answerText(row)}</p>
      </div>}
      {state === 'incorrect' && <div className="recognition-correct-answer">
        <span className="recognition-label">正确答案</span>
        <p>{decision?.correct_answer?.trim() || '暂未生成正确答案，请重新识别或请老师核对。'}</p>
      </div>}
      <div className="recognition-learning">
        {!row.question.subject && subjects.length > 0 && <div className="recognition-subject-picker">
          <span className="recognition-label">无法确定学科</span>
          <div><select aria-label="选择题目学科" disabled={!!workspace?.busy} defaultValue="" onChange={event => { if (event.target.value) onSelectSubject(event.target.value) }}>
            <option value="">请选择本题学科</option>
            {subjects.map(subject => <option key={subject.subject} value={subject.subject}>{subject.subject} · {subject.publisher_name}</option>)}
          </select><small>仅显示当前年级已配置且官方目录匹配的最新版教材。</small></div>
        </div>}
        <div className="recognition-learning-row"><span>知识点</span><div className="knowledge-tags">{points.length ? points.map(point => <span key={point}>{point}</span>) : <em>暂未识别</em>}</div></div>
        {solutionSteps?.trim() && usable && decision && ['incorrect', 'partially_correct', 'unanswered'].includes(decision.verdict) && <div className="recognition-learning-row correction-method"><span><Lightbulb size={15} />正确解题步骤</span><p className="recognition-solution-steps">{solutionSteps}</p></div>}
      </div>
      {state !== 'unanswered' && uncertainties.length > 0 && <p className="recognition-uncertainty">{controlledServiceMessage ?? '* AI 对这道题的识别或判断存在不确定，请结合原图核对。'}</p>}
    </div>}
  </article>
}

export function RecognitionResults({ rows, workspace, onNext, onSelectSubject, subjects: suppliedSubjects, onSelect }: RecognitionResultsProps) {
  const [openId, setOpenId] = useState<string | null>(null)
  const subjects = (suppliedSubjects ?? workspace.studentCurriculum?.subjects ?? []).filter(subject => subject.catalog_status === 'available')
  return <div className="recognition-results">
    <ResultsSummary rows={rows} detail={null} compact />
    <p className="recognition-legend">按照片中的位置排列，序号连续显示。点击题目查看作答、正确答案、知识点和正确解题步骤，正确/错误右上角的 * 表示 AI 识别存在不确定。</p>
    <div className="recognition-card-list" aria-label="本次识别的题目">{rows.map((row, index) => <QuestionCard key={row.case.id} row={row} index={index} workspace={workspace} subjects={subjects} onSelectSubject={subject => onSelectSubject(row, subject)} open={openId === row.case.id} onToggle={() => { onSelect?.(row); setOpenId(current => current === row.case.id ? null : row.case.id) }} />)}</div>
    <div className="next-capture-wrap"><button className="button primary next-capture" type="button" disabled={!!workspace.busy} onClick={onNext}><Camera size={18} />拍下一题</button></div>
  </div>
}
