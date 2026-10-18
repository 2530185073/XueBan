import { useState } from 'react'
import { Camera, Check, ChevronDown, CircleAlert, CircleX, Lightbulb } from 'lucide-react'
import type { CaseDetail } from '../core/types'
import type { Workspace } from '../useStudentWorkspace'
import { effectiveDecision } from '../core'
import { studentResultState, type StudentResultState } from '../presentation'
import { ResultsSummary } from './ResultsSummary'

interface RecognitionResultsProps {
  rows: CaseDetail[]
  workspace: Workspace
  onNext: () => void
}

const stateLabels: Record<StudentResultState, string> = {
  correct: '正确',
  incorrect: '错误',
  uncertain: '待确认',
}

const reasonMethods: Record<string, string> = {
  sign: '重新检查正负号，负数先加括号，再按运算顺序逐步计算。',
  operation: '先判断题目中两个量的关系，再确认应该使用哪一种运算。',
  distribution: '把括号外的因数分别乘到括号内每一项，检查有没有漏项。',
  fraction: '先确认分数线包含的范围，再分别检查分子和分母的运算。',
  units: '把每一步的单位写清楚，先统一单位，再检查数量关系。',
  substitution: '把已知数逐个代入对应字母，负数代入时先加括号。',
  diagram: '回到图形中逐一核对边、角和标记，再检查计算使用的条件。',
  steps: '从第一处变化开始逐行检查，每一步只做一个运算并保留中间结果。',
  insufficient_process: '补齐题目要求的关键步骤，再检查每一步是否由上一行正确得到。',
}

function fallbackMethod(row: CaseDetail) {
  const decision = effectiveDecision(row)
  return decision?.reason_code ? reasonMethods[decision.reason_code] : undefined
}

function answerText(row: CaseDetail) {
  if (row.attempt.answer_status === 'blank') return '未填写作答'
  return row.attempt.answer_text || '暂未读到清楚的作答'
}

function ResultIcon({ state }: { state: StudentResultState }) {
  if (state === 'correct') return <Check size={17} strokeWidth={2.5} />
  if (state === 'incorrect') return <CircleX size={17} strokeWidth={2.2} />
  return <CircleAlert size={17} strokeWidth={2.2} />
}

function QuestionCard({ row, index, open, onToggle }: { row: CaseDetail; index: number; open: boolean; onToggle: () => void }) {
  const state = studentResultState(row)
  const decision = effectiveDecision(row)
  const uncertain = state === 'uncertain'
  const options = row.question.options ?? []
  const method = decision?.correction_method?.trim() || fallbackMethod(row)
  const points = decision?.knowledge_points ?? []
  const uncertainties = [...new Set([
    ...(row.question.recognition_uncertainties ?? []),
    ...(row.attempt.recognition_uncertainties ?? []),
    ...(decision?.uncertainties ?? []),
  ])]
  return <article className={`recognition-card ${state} ${open ? 'open' : ''}`}>
    <button className="recognition-card-head" type="button" aria-expanded={open} onClick={onToggle}>
      <span className="recognition-index">{index + 1}</span>
      <span className="recognition-card-title">{row.question.stem_text || '题干待核实'}{uncertain && <span className="uncertain-star" aria-label="AI识别不确定">*</span>}</span>
      <span className={`recognition-status ${state}`}><ResultIcon state={state} />{stateLabels[state]}</span>
      <ChevronDown className="recognition-chevron" size={19} aria-hidden="true" />
    </button>
    {open && <div className="recognition-card-body">
      <div className="recognition-question-block">
        <span className="recognition-label">题目</span>
        <p>{row.question.stem_text || '题干暂未读清。'}</p>
        {options.length > 0 && <ul className="recognition-options" aria-label="题目选项">{options.map(option => <li key={`${option.label}:${option.text}`}><b>{option.label}</b><span>{option.text}</span></li>)}</ul>}
        {!!row.question.blank_count && <span className="blank-note">填空题 · {row.question.blank_count} 个空</span>}
      </div>
      <div className={`recognition-answer ${state}`}>
        <span className="recognition-label">我的作答</span>
        <p>{answerText(row)}</p>
      </div>
      <div className="recognition-learning">
        <div className="recognition-learning-row"><span>知识点</span><div className="knowledge-tags">{points.length ? points.map(point => <span key={point}>{point}</span>) : <em>暂未识别</em>}</div></div>
        {(state === 'incorrect' || uncertain) && <div className="recognition-learning-row correction-method"><span><Lightbulb size={15} />改正方法</span><p>{method || '请重新读题，逐步检查题目条件、运算符号和最终作答。'}</p></div>}
      </div>
      {uncertainties.length > 0 && <p className="recognition-uncertainty">* AI 对这道题的识别或判断存在不确定，请结合原图核对。</p>}
    </div>}
  </article>
}

export function RecognitionResults({ rows, workspace, onNext }: RecognitionResultsProps) {
  const [openId, setOpenId] = useState<string | null>(null)
  return <div className="recognition-results">
    <ResultsSummary rows={rows} detail={null} compact />
    <p className="recognition-legend">点击题目查看选项、作答、知识点和改正方法，题目末尾的 * 表示 AI 识别不确定。</p>
    <div className="recognition-card-list" aria-label="本次识别的题目">{rows.map((row, index) => <QuestionCard key={row.case.id} row={row} index={index} open={openId === row.case.id} onToggle={() => setOpenId(current => current === row.case.id ? null : row.case.id)} />)}</div>
    <div className="next-capture-wrap"><button className="button primary next-capture" type="button" disabled={!!workspace.busy} onClick={onNext}><Camera size={18} />拍下一题</button></div>
  </div>
}
