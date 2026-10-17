import type { CaseDetail, ImageAsset, Job } from './types'

/**
 * The homework's own pages in page order. The homework lists every asset,
 * sorted by id, including correction photos uploaded later. Pages are the
 * uploaded assets that existed when recognition first started or that a
 * question/original answer points at; photos of a correction attempt are not pages.
 */
export function homeworkPageIds(assets: ImageAsset[], jobs: Job[], rows: CaseDetail[]): string[] {
  const uploaded = assets.filter(a => a.status === 'uploaded' && !a.source_asset_id)
  const corrections = new Set(rows.filter(r => r.attempt.parent_attempt_id).flatMap(r => r.attempt.asset_ids))
  const referenced = new Set(rows.flatMap(r => [...r.question.regions.map(region => region.asset_id), ...(r.attempt.parent_attempt_id ? [] : r.attempt.asset_ids)]))
  const started = jobs.filter(j => j.kind === 'recognize_homework').map(j => Date.parse(j.created_at)).filter(Number.isFinite)
  const first = started.length ? Math.min(...started) : Infinity
  const pages = uploaded.filter(a => referenced.has(a.id) || !corrections.has(a.id) && Date.parse(a.created_at) <= first)
  return (pages.length ? pages : uploaded).sort((a, b) => a.page_index - b.page_index || a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id)).map(a => a.id)
}
