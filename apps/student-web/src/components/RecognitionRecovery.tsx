import { useEffect, useRef, useState } from 'react'
import { RefreshCw, ScanText } from 'lucide-react'
import type { CaseDetail, RecognitionRecoveryStatus } from '../core/types'
import { RecognitionRecoveryController, initialRecoveryView, type RecoveryView } from '../core/recognitionRecovery'
import type { Workspace } from '../useStudentWorkspace'
import './recognitionRecovery.css'

const outcomes: Record<NonNullable<RecognitionRecoveryStatus['last_outcome']>, string> = {
  applied: '原图核对已完成，这道题的记录已更新。新的判断仍是 AI 初判。',
  unchanged_pending: '这次仍有看不准的地方，暂不判断对错。原记录已保留。',
  target_unconfirmed: '新读到的内容还不能确认属于同一道题，原记录没有替换。',
  scope_change_required: '新读到的范围与这道题不一致，原记录没有替换。',
  service_failed: '这次核对服务未完成，原图和记录已保留。',
  stale_input: '题目或作答已经更新，请查看最新记录。',
  execution_unknown: '这次执行结果还无法确认，系统没有自动重复核对。',
}
const blocked: Partial<Record<NonNullable<RecognitionRecoveryStatus['blocked_reason']>, string>> = {
  staff_confirmed: '这道题已经由老师确认，无需再做原图核对。',
  already_resolved: '这道题已有明确结果，可继续订正或查看其他题。',
  initial_recognition_pending: '第一次识读还在进行，完成后再查看这道题。',
  multiple_source_pages: '这份作答涉及多张照片，请补拍这道题的清晰照片或请老师帮忙。',
  inactive_case: '这道题的记录已更新，请刷新查看当前题目。',
}
export function RecognitionRecoveryCard({ view, needed, online, busy, onStart, onRefresh }: { view: RecoveryView; needed: boolean; online: boolean; busy: boolean; onStart: () => void; onRefresh: () => void }) {
  const status = view.status, active = !!status?.active_job_id
  if (!needed && !status?.used && !active) return null
  const exhausted = status?.blocked_reason === 'budget_exhausted'
  const message = active ? 'AI 正在重新看这张原图。可以先看其他题，回来时会接着显示进度。' : exhausted ? '这份作答的原图核对次数已用完。可以先看其他题，或补拍清楚的照片、请老师帮忙。' : status?.last_outcome ? outcomes[status.last_outcome] : status?.blocked_reason ? blocked[status.blocked_reason] : '让 AI 再看一次当前原图，不需要重新作答，也不会记成一次新作答。'
  return <section className="recognition-recovery" aria-label="原图核对">
    <div className="recovery-heading"><ScanText size={19} /><h3>再看清楚一点</h3>{status && <span>已用 {status.used} / {status.limit} 次</span>}</div>
    <p role="status" aria-live="polite">{message}</p>
    {exhausted && status?.last_outcome && <p className="muted small">上次核对：{outcomes[status.last_outcome]}</p>}
    {view.error && <p className="recovery-error" role="alert">{view.error} 请先刷新状态确认进度。</p>}
    {!online && <p className="muted small">当前离线，联网后可查看核对进度。</p>}
    <div className="recovery-actions">
      <button className="button secondary" disabled={!online || busy || view.loading || view.submitting || active || !status?.eligible || !!view.error} onClick={onStart}><RefreshCw size={16} className={active || view.submitting ? 'spin' : undefined} />{view.submitting ? '正在提交核对' : active ? '正在核对原图' : '重新核对原图'}</button>
      <button className="text-button" disabled={!online || view.loading || view.submitting} onClick={onRefresh}>{view.loading ? '正在读取状态' : '刷新状态'}</button>
      {status?.eligible && <small>还可核对 {status.remaining} 次</small>}
    </div>
  </section>
}
export function RecognitionRecoveryPanel({ row, workspace, needed }: { row: CaseDetail; workspace: Workspace; needed: boolean }) {
  const [view, setView] = useState<RecoveryView>(initialRecoveryView)
  const controller = useRef<RecognitionRecoveryController | null>(null)
  const workspaceRef = useRef(workspace); workspaceRef.current = workspace
  useEffect(() => {
    if (!workspace.online) return
    const next = new RecognitionRecoveryController(workspace.client, row, setView,
      current => workspaceRef.current.refreshRecognition(row, current),
      error => workspaceRef.current.report(error))
    controller.current = next; void next.load()
    return () => { next.dispose(); controller.current = null }
  }, [workspace.client, workspace.online, row.case.id, row.case.revision, row.attempt.id, row.case.current_question_revision, row.attempt.recognition_revision])
  return <RecognitionRecoveryCard view={view} needed={needed} online={!!workspace.online} busy={!!workspace.busy} onStart={() => void controller.current?.start()} onRefresh={() => void controller.current?.load()} />
}
