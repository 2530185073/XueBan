import type { Attempt, AttemptRead, CapturedQuestionRevision, CheckRun, CorrectionCase, CurriculumContext, DecisionRead, HelpRequest, Hint, HomeworkSession, Job, RecordLifecycle, Review } from '../../../staff-web/src/api/contracts.generated'
export type * from '../../../staff-web/src/api/contracts.generated'
import type { Crop, Rotation } from './pages'

export type DraftStage = 'local' | 'creating' | 'uploading' | 'recognizing' | 'checking' | 'complete' | 'failed' | 'waiting'
export interface CaseDetail { case: CorrectionCase; question: CapturedQuestionRevision; attempt: Attempt; checkJob?: Job }
/** A case the staff retired (layout repair or deletion); never listed as current work. */
export interface RetiredCase { id: string; lifecycle: Exclude<RecordLifecycle, 'active'>; replacementCaseIds: string[] }
export interface HomeworkDetail { homework: HomeworkSession; cases: CaseDetail[]; jobs: Job[]; draftId?: string; retired?: RetiredCase[] }
/** Every server record reachable from one case, read-only, for the version timeline. */
export interface CaseRecord { case: CorrectionCase; attempts: AttemptRead[]; checks: CheckRun[]; decisions: DecisionRead[]; hints: Hint[]; help: HelpRequest[]; reviews: Review[] }
export type CaseBucket = 'needs_correction' | 'pending_review' | 'candidate_correct' | 'confirmed_correct'
export interface CaseCounts { total: number; needs_correction: number; pending_review: number; candidate_correct: number; confirmed_correct: number }
export interface HomeworkSummary { homeworkId: string; revision: number; counts: CaseCounts; firstUnresolvedCaseId: string | null; retired: number }
export interface SafeError { code: string; message: string }
export interface DraftSummary {
  id: string; title: string; stage: DraftStage; createdAt: string; updatedAt: string
  homeworkId: string | null; error: SafeError | null
  /** Pages held on this device and how many of them the server has confirmed as uploaded. */
  pageCount: number; uploadedCount: number
}
export interface DraftPageView { id: string; blob: Blob; width: number; height: number; size: number; edited: boolean; assetId?: string; uploaded: boolean }
export interface DraftView extends DraftSummary {
  /** First page, kept for single-photo callers. */
  photo: Blob | null; pages: DraftPageView[]
  /** Pages may still be added, removed, reordered, turned or cropped: nothing has been sent yet. */
  editable: boolean
  detail: HomeworkDetail | null; curriculum?: CurriculumContext | null; correction?: { caseId: string; parentAttemptId: string }
}
export interface NormalizedPhoto { blob: Blob; width: number; height: number; sha256: string; mime: 'image/jpeg'; size: number }
export interface StoredPage {
  id: string; photo: NormalizedPhoto
  /** The imported photo when `photo` is a turned/cropped render of it. */
  original?: NormalizedPhoto; rotation: Rotation; crop: Crop | null
  assetId?: string; uploaded: boolean; ticketGeneration: number
}
export interface StoredDraft extends Omit<DraftSummary, 'pageCount' | 'uploadedCount'> {
  format: 2
  curriculum?: CurriculumContext | null
  scope: string; pages: StoredPage[]; locked: boolean; detail: HomeworkDetail | null; generation: number
  journalId?: string
  recognitionJobId?: string; checkJobs: Record<string, string>
  correction?: { caseId: string; parentAttemptId: string; reportedHelp: 'unknown' | 'none_reported' | 'staff_help' | 'answer_shown' }
}
/** Format-1 record: one photo with its upload state on the draft itself. */
export interface LegacyDraft extends Omit<StoredDraft, 'format' | 'pages' | 'locked'> { format?: undefined; photo: NormalizedPhoto | null; assetId?: string; ticketGeneration?: number }
export interface JournalEntry { scope: string; id: string; key: string; path: string; body: string; shape: string; response?: unknown; updatedAt?: string }

export interface Account { username: string; must_change_password: boolean; student_id: string | null }
export interface AuthSession { access_token: string; expires_at: string; must_change_password: boolean }
export interface AdminRequestOptions { method?: 'GET' | 'POST'; body?: unknown; idempotencyKey?: string }
