/** Read-only, independently versioned projection; never a Case/Decision. */
export interface RecognitionProgress {
  protocol: 'recognition-draft.v1'; job_id: string; homework_id: string; student_id: string
  state: 'unavailable' | 'reading' | 'checking' | 'final_available'
  pages: { asset_id: string; completed_reads: number; draft: { label: string; stem_text: string | null; answer_text: string | null }[] }[]
}
const obj = (x: unknown): x is Record<string, unknown> => !!x && typeof x === 'object' && !Array.isArray(x)
const keys = (x: Record<string, unknown>, names: string[]) => Object.keys(x).length === names.length && names.every(k => k in x)
const text = (x: unknown, n: number) => typeof x === 'string' && x.length <= n
export function isRecognitionProgress(x: unknown): x is RecognitionProgress {
  if (!obj(x) || !keys(x, ['protocol', 'job_id', 'homework_id', 'student_id', 'state', 'pages']) || x.protocol !== 'recognition-draft.v1' || !['job_id', 'homework_id', 'student_id'].every(k => text(x[k], 200) && !!x[k]) || !['unavailable', 'reading', 'checking', 'final_available'].includes(String(x.state)) || !Array.isArray(x.pages) || x.pages.length > 4) return false
  if (['unavailable', 'final_available'].includes(String(x.state)) && x.pages.length) return false
  const seen = new Set<string>()
  return x.pages.every(p => {
    if (!obj(p) || !keys(p, ['asset_id', 'completed_reads', 'draft']) || !text(p.asset_id, 200) || !p.asset_id || seen.has(p.asset_id as string) || ![1, 2].includes(Number(p.completed_reads)) || typeof p.completed_reads !== 'number' || !Array.isArray(p.draft) || !p.draft.length || p.draft.length > 80) return false
    seen.add(p.asset_id as string)
    return p.draft.every(d => obj(d) && keys(d, ['label', 'stem_text', 'answer_text']) && text(d.label, 160) && (d.stem_text === null || text(d.stem_text, 8000)) && (d.answer_text === null || text(d.answer_text, 4000)))
  })
}
