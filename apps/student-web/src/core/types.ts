import type { Attempt, CapturedQuestionRevision, CorrectionCase, HomeworkSession, Job } from '../../../staff-web/src/api/contracts.generated'
export type * from '../../../staff-web/src/api/contracts.generated'

export type DraftStage = 'local' | 'creating' | 'uploading' | 'recognizing' | 'checking' | 'complete' | 'failed' | 'waiting'
export interface CaseDetail { case: CorrectionCase; question: CapturedQuestionRevision; attempt: Attempt }
export interface HomeworkDetail { homework: HomeworkSession; cases: CaseDetail[]; jobs: Job[]; draftId?: string }
export interface SafeError { code: string; message: string }
export interface DraftSummary {
  id: string; title: string; stage: DraftStage; createdAt: string; updatedAt: string
  homeworkId: string | null; error: SafeError | null
}
export interface DraftView extends DraftSummary { photo: Blob | null; detail: HomeworkDetail | null; correction?: { caseId: string; parentAttemptId: string } }
export interface NormalizedPhoto { blob: Blob; width: number; height: number; sha256: string; mime: 'image/jpeg'; size: number }
export interface StoredDraft extends DraftSummary {
  scope: string; photo: NormalizedPhoto | null; detail: HomeworkDetail | null; generation: number
  journalId?: string
  assetId?: string; recognitionJobId?: string; ticketGeneration: number; checkJobs: Record<string, string>
  correction?: { caseId: string; parentAttemptId: string; reportedHelp: 'unknown' | 'none_reported' | 'staff_help' | 'answer_shown' }
}
export interface JournalEntry { scope: string; id: string; key: string; path: string; body: string; shape: string; response?: unknown }
