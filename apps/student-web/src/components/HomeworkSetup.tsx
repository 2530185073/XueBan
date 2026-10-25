import { BookOpen, RefreshCw } from 'lucide-react'
import type { Workspace } from '../useStudentWorkspace'
import { homeworkSetupIssue } from '../core/homeworkSetup'
import { CurriculumBookPicker } from './CurriculumBookPicker'

/** Keep the required setup beside capture, including when a local photo is already selected. */
export function HomeworkSetup({ workspace }: { workspace: Workspace }) {
  const issue = homeworkSetupIssue(workspace.studentCurriculum, workspace.studentBooks)
  if (!issue) return null
  const grade = workspace.studentCurriculum?.grade
  return <section className="homework-setup" aria-label="检查前的课程设置">
    <div className="homework-setup-heading"><BookOpen size={22} /><div><h2>先对齐学校的课程</h2><p role="status">{issue}</p></div></div>
    <div className="homework-setup-controls"><label htmlFor="homework-grade">当前年级</label><select id="homework-grade" value={grade ?? ''} disabled={!!workspace.busy || !workspace.online} onChange={event => { const next = Number(event.target.value); if (next) void workspace.setStudentGrade(next) }}><option value="">请选择年级</option>{[5, 6, 7, 8].map(value => <option value={value} key={value}>{value <= 6 ? `小学${value === 5 ? '五' : '六'}年级` : `初${value - 6}`}</option>)}</select><button className="text-button" disabled={!!workspace.busy || !workspace.online} onClick={() => void workspace.refreshCurriculum()}><RefreshCw size={15} />刷新课程设置</button></div>
    {grade && <CurriculumBookPicker workspace={workspace} grade={grade} onChanged={() => void workspace.refreshCurriculum()} />}
    {workspace.draft && !workspace.draft.homeworkId && <p className="muted small">已选照片仍保存在这台设备上，设置好后就能继续。</p>}
  </section>
}
