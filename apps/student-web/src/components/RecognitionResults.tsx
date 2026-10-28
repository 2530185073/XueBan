import { useEffect, useRef, useState } from 'react'
import { Camera, Check, ChevronDown, Circle, CircleAlert, CircleX, Clock3, Lightbulb, LoaderCircle } from 'lucide-react'
import type { CaseDetail } from '../core/types'
import type { CurriculumScope } from '../core/learning'
import type { Workspace } from '../useStudentWorkspace'
import { effectiveDecision } from '../core'
import { studentQuestionText, studentResultState, studentResultUncertain, studentUnresolvedKind, type StudentResultState } from '../presentation'
import { ResultsSummary } from './ResultsSummary'
import { MathText } from './MathText'
import { prepareResultCardOpen, resultCardBinding, resultCardSolutionUsable } from '../core/resultDisclosure'
import { safeError } from '../core/errors'

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
  opening?: boolean
  openError?: string
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

export function QuestionCard({ row, index, open, onToggle, subjects = [], onSelectSubject = () => {}, workspace, opening = false, openError }: QuestionCardProps) {
  const serviceIncomplete = studentUnresolvedKind(row) === 'service'
  const state = serviceIncomplete ? 'uncertain' : studentResultState(row)
  const decision = effectiveDecision(row)
  const usable = resultCardSolutionUsable(row) && decision
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
    <button className="recognition-card-head" type="button" aria-expanded={open} aria-busy={opening || undefined} onClick={onToggle}>
      <span className="recognition-index">{index + 1}</span>
      <span className="recognition-card-title">{stemText || '题干待核实'}</span>
      <span className={`recognition-status ${state}`}>{uncertain && <sup className="result-star" aria-label="AI识别不确定">*</sup>}<ResultIcon state={state} />{serviceIncomplete ? '未完成' : stateLabels[state]}</span>
      {opening ? <LoaderCircle className="recognition-chevron spin" size={19} aria-label="正在打开题目" /> : <ChevronDown className="recognition-chevron" size={19} aria-hidden="true" />}
    </button>
    {openError && <p className="recognition-uncertainty" role="alert">{openError} 点击题目重试。</p>}
    {open && <div className="recognition-card-body">
      <div className="recognition-question-block">
        <span className="recognition-label">题目</span>
        <p><MathText text={stemText || '题干暂未读清。'} /></p>
        {!!row.question.blank_count && <span className="blank-note">填空题 · {row.question.blank_count} 个空</span>}
      </div>
      {!!row.question.options?.length && <ol className="recognition-original-options" aria-label="原题选项">{row.question.options.map((option, optionIndex) => <li key={`${optionIndex}:${option.label}`}><strong>{option.label}</strong><span><MathText text={option.text} /></span></li>)}</ol>}
      {usable && decision.answer_checks?.length ? <div className="recognition-answer-slots" aria-label="逐空判定">
        {decision.answer_checks.map(part => <div key={part.index} className={`recognition-answer ${slotFeedback[part.verdict].tone}`}>
          <span className="recognition-label">第{part.index}{row.question.blank_count ? '空' : '项'} · <MathText text={part.prompt} /></span>
          <p><MathText text={part.student_answer || (part.verdict === 'unanswered' ? '未作答' : '暂未读到清楚的作答')} /> <span className="slot-verdict">{slotFeedback[part.verdict].label}</span></p>
          {part.verdict === 'incorrect' && part.correct_answer && <p className="slot-correction">应填：<MathText text={part.correct_answer} /></p>}
        </div>)}
      </div> : <div className={`recognition-answer ${state}`}>
        <span className="recognition-label">{serviceIncomplete ? '识别内容' : '我的作答'}</span>
        <p><MathText text={serviceIncomplete ? row.attempt.answer_text || '暂未读清作答' : answerText(row)} /></p>
      </div>}
      {state === 'incorrect' && usable && <div className="recognition-correct-answer">
        <span className="recognition-label">正确答案</span>
        <p><MathText text={decision?.correct_answer?.trim() || '暂未生成正确答案，请重新识别或请老师核对。'} /></p>
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
        {solutionSteps?.trim() && usable && decision && ['incorrect', 'partially_correct', 'unanswered'].includes(decision.verdict) && <div className="recognition-learning-row correction-method"><span><Lightbulb size={15} />正确解题步骤</span><p className="recognition-solution-steps"><MathText text={solutionSteps} /></p></div>}
      </div>
      {state !== 'unanswered' && (uncertainties.length > 0 || serviceIncomplete) && <p className="recognition-uncertainty">{serviceIncomplete ? '核对尚未完成，原图已保留。' : controlledServiceMessage ?? '* AI 对这道题的识别或判断存在不确定，请结合原图核对。'}</p>}
    </div>}
  </article>
}

export function RecognitionResults({ rows, workspace, onNext, onSelectSubject, subjects: suppliedSubjects, onSelect }: RecognitionResultsProps) {
  const [opened, setOpened] = useState<string | null>(null)
  const [opening, setOpening] = useState<string | null>(null)
  const [openError, setOpenError] = useState<{ binding: string; message: string } | null>(null)
  const sequence = useRef(0), mounted = useRef(true)
  const scope = workspace.client?.evidenceScope
  const latest = useRef({ rows, scope }); latest.current = { rows, scope }
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; sequence.current++ } }, [])
  useEffect(() => {
    const bindings = new Set(rows.map(row => resultCardBinding(row, scope)))
    // Rechecking and switching accounts permanently revoke the old expansion;
    // returning to the previous state must not reopen an answer on its own.
    if (opened && !bindings.has(opened)) setOpened(null)
    if (opening && !bindings.has(opening)) { sequence.current++; setOpening(null) }
    if (openError && !bindings.has(openError.binding)) setOpenError(null)
  }, [rows, scope, opened, opening, openError])
  const toggle = async (row: CaseDetail) => {
    const binding = resultCardBinding(row, scope), request = ++sequence.current
    setOpenError(null)
    if (opened === binding || opening === binding) { setOpened(null); setOpening(null); return }
    setOpened(null); setOpening(binding)
    const stillCurrent = () => mounted.current && sequence.current === request
      && workspace.client?.evidenceScope === scope && latest.current.scope === scope
      && latest.current.rows.some(current => resultCardBinding(current, scope) === binding)
    try {
      if (await prepareResultCardOpen(row, current => workspace.client.recordSolutionViewed(current), stillCurrent)) {
        setOpened(binding); onSelect?.(row)
      }
    } catch (error) {
      if (stillCurrent()) { setOpenError({ binding, message: safeError(error).message }); workspace.report(error) }
    } finally { if (mounted.current && sequence.current === request) setOpening(null) }
  }
  const subjects = (suppliedSubjects ?? workspace.studentCurriculum?.subjects ?? []).filter(subject => subject.catalog_status === 'available')
  return <div className="recognition-results">
    <ResultsSummary rows={rows} detail={null} compact />
    <p className="recognition-legend">按照片中的位置排列，序号连续显示。点击题目查看作答、正确答案、知识点和正确解题步骤，正确/错误右上角的 * 表示 AI 识别存在不确定。</p>
    <div className="recognition-card-list" aria-label="本次识别的题目">{rows.map((row, index) => {
      const binding = resultCardBinding(row, scope)
      return <QuestionCard key={row.case.id} row={row} index={index} workspace={workspace} subjects={subjects} onSelectSubject={subject => onSelectSubject(row, subject)} open={opened === binding} opening={opening === binding} openError={openError?.binding === binding ? openError.message : undefined} onToggle={() => void toggle(row)} />
    })}</div>
    <div className="next-capture-wrap"><button className="button primary next-capture" type="button" disabled={!!workspace.busy} onClick={onNext}><Camera size={18} />拍下一题</button></div>
  </div>
}
