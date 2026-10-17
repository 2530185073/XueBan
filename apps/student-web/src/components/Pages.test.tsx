import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import examples from '../../../../contracts/phase1/examples.json'
import type { Attempt, CaseDetail, DraftPageView, DraftView, ImageAsset, Job } from '../core/types'
import type { Workspace } from '../useStudentWorkspace'
import { homeworkPageIds } from '../core/homeworkPages'
import { uploadState } from '../presentation'
import { PageNav, pageSources } from './PhotoPanel'
import { DraftPages, marginsToCrop } from './DraftPages'
import { ResumeDrafts } from './ResumeDrafts'

const fixture = <T,>(name: string) => structuredClone(examples.entries.find(x => x.name === name)!.value) as T
const asset = (id: string, page_index: number, created_at: string, status: ImageAsset['status'] = 'uploaded'): ImageAsset => ({ ...fixture<ImageAsset>('asset'), id, page_index, created_at, status, source_asset_id: null, source_region: null })
const job = (created_at: string): Job => ({ id: 'job_recognition', revision: 1, kind: 'recognize_homework', status: 'succeeded', created_at, updated_at: created_at, retry_after_seconds: null, failure: null, result: null })
const localPage = (id: string, patch: Partial<DraftPageView> = {}): DraftPageView => ({ id, blob: new Blob(['x'], { type: 'image/jpeg' }), width: 30, height: 20, size: 1_048_576, edited: false, uploaded: false, ...patch })
const draftOf = (pages: DraftPageView[], patch: Partial<DraftView> = {}): DraftView => ({ id: 'draft_one', title: '学校作业', stage: 'local', createdAt: '2026-10-17T00:00:00Z', updatedAt: '2026-10-17T00:00:00Z', homeworkId: null, error: null, pageCount: pages.length, uploadedCount: pages.filter(p => p.uploaded).length, photo: pages[0]?.blob ?? null, pages, editable: true, detail: null, ...patch })
const workspace = { busy: '', drafts: [], client: { objectUrl: () => 'blob:synthetic', releaseObjectUrl: () => undefined } } as unknown as Workspace

describe('homework pages for history', () => {
  it('orders pages by page index and drops photos uploaded after recognition, unfinished uploads and correction photos', () => {
    const row = { question: { ...fixture<CaseDetail['question']>('question_revision_2'), regions: [] }, attempt: { ...fixture<Attempt>('correction_across_question_revision'), parent_attempt_id: 'attempt_root', asset_ids: ['asset_fix'] } } as unknown as CaseDetail
    const assets = [asset('asset_c', 2, '2026-10-17T00:00:03Z'), asset('asset_a', 0, '2026-10-17T00:00:01Z'), asset('asset_b', 1, '2026-10-17T00:00:02Z'), asset('asset_lost', 3, '2026-10-17T00:00:04Z', 'awaiting_upload'), asset('asset_fix', 3, '2026-10-18T00:00:00Z'), asset('asset_later', 4, '2026-10-19T00:00:00Z')]
    expect(homeworkPageIds(assets, [job('2026-10-17T00:01:00Z')], [row])).toEqual(['asset_a', 'asset_b', 'asset_c'])
    expect(homeworkPageIds(assets.slice(0, 3), [], [])).toEqual(['asset_a', 'asset_b', 'asset_c'])
  })
  it('pages through every page with page i / n, and adds the answer\'s correction photo as its own tab', () => {
    const row = { attempt: { asset_ids: ['asset_fix'], parent_attempt_id: 'attempt_root' } } as unknown as CaseDetail
    const { sources, pageCount } = pageSources(null, ['asset_a', 'asset_b', 'asset_c'], row)
    expect(pageCount).toBe(3); expect(sources.map(s => s.label)).toEqual(['第 1 页', '第 2 页', '第 3 页', '订正照片'])
    const html = renderToStaticMarkup(<PageNav sources={sources} pageCount={pageCount} index={1} onSelect={() => {}} />)
    expect(html).toContain('第 2 / 3 页'); expect(html).toContain('aria-label="上一页"'); expect(html).toContain('aria-label="下一页"')
    expect(html.match(/aria-pressed="true"/g)).toHaveLength(1); expect(html).toContain('>订正照片</button>')
    expect(renderToStaticMarkup(<PageNav sources={sources} pageCount={pageCount} index={3} onSelect={() => {}} />)).toContain('<span class="page-count" aria-live="polite">订正照片</span>')
    expect(renderToStaticMarkup(<PageNav sources={sources.slice(0, 1)} pageCount={1} index={0} onSelect={() => {}} />)).toBe('')
  })
  it('shows this device\'s copies of a draft in its saved order, before the server has them', () => {
    const draft = draftOf([localPage('p2', { assetId: 'asset_2', uploaded: true }), localPage('p1')], { stage: 'failed', editable: false })
    const { sources, pageCount } = pageSources(draft, null)
    expect(pageCount).toBe(2); expect(sources.map(s => [s.key, s.uploaded])).toEqual([['p2', true], ['p1', false]])
  })
})

describe('page editor', () => {
  it('renders a multi-page draft with order, turn, crop, delete, add and the explicit limits', () => {
    const html = renderToStaticMarkup(<DraftPages workspace={workspace} draft={draftOf([localPage('p1'), localPage('p2')])} onAdd={() => {}} onReplace={() => {}} />)
    for (const text of ['检查这 2 页', '第 1 / 2 页', '只在这台设备', '往前移一页', '往后移一页', '向左转 90 度', '向右转 90 度', '裁切这一页', '换一张', '删除', '加一页', '拍一页', '共 2 页 · 2.0 MiB / 8 MiB', '最多 4 页 · 每页不超过 4 MiB · 合计不超过 8 MiB']) expect(html).toContain(text)
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*aria-label="往前移一页"/)
  })
  it('hides adding at 4 pages and for a single correction photo', () => {
    const full = renderToStaticMarkup(<DraftPages workspace={workspace} draft={draftOf(['a', 'b', 'c', 'd'].map(id => localPage(id)))} onAdd={() => {}} onReplace={() => {}} />)
    expect(full).not.toContain('从相册再加一页'); expect(full).toContain('已经 4 页了')
    const correction = renderToStaticMarkup(<DraftPages workspace={workspace} draft={draftOf([localPage('c')], { correction: { caseId: 'case_one', parentAttemptId: 'attempt_one' } })} onAdd={() => {}} onReplace={() => {}} />)
    expect(correction).toContain('看清这张订正照片'); expect(correction).not.toContain('加一页'); expect(correction).not.toContain('往前移一页'); expect(correction).toContain('向右转 90 度')
  })
  it('turns side margins into a crop of the shown page', () => {
    expect(marginsToCrop({ top: 0, bottom: 0, left: 0, right: 0 })).toBeNull()
    expect(marginsToCrop({ top: 10, bottom: 20, left: 5, right: 15 })).toEqual({ x: 0.05, y: 0.1, width: 0.8, height: 0.7 })
  })
})

describe('local versus received', () => {
  it('labels drafts by where their photos are', () => {
    expect(uploadState({ stage: 'local', homeworkId: null, pageCount: 3, uploadedCount: 0 }).label).toBe('3 页照片只在这台设备，还没上传')
    expect(uploadState({ stage: 'failed', homeworkId: 'homework_one', pageCount: 3, uploadedCount: 1 }).label).toBe('已上传 1 / 3 页，其余还在这台设备')
    expect(uploadState({ stage: 'recognizing', homeworkId: 'homework_one', pageCount: 2, uploadedCount: 2 })).toEqual({ label: '服务器已收到照片，正在处理', tone: 'neutral' })
    expect(uploadState({ stage: 'complete', homeworkId: 'homework_one', pageCount: 0, uploadedCount: 0 }).tone).toBe('green')
  })
  it('lists unfinished drafts to continue, with delete only for ones that sent nothing', () => {
    const drafts = [{ ...draftOf([localPage('a'), localPage('b')]), id: 'draft_local' }, { ...draftOf([localPage('c', { uploaded: true })], { stage: 'failed', homeworkId: 'homework_one' }), id: 'draft_sent', title: '数学作业' }, { ...draftOf([], { stage: 'complete' }), id: 'draft_done' }]
    const html = renderToStaticMarkup(<ResumeDrafts workspace={{ ...workspace, drafts } as unknown as Workspace} onOpen={() => {}} />)
    expect(html).toContain('上次没做完的'); expect(html).toContain('2 页照片只在这台设备，还没上传'); expect(html).toContain('服务器已收到照片，正在处理')
    expect(html.match(/删除草稿/g)).toHaveLength(1); expect(html.match(/继续/g)).toHaveLength(2)
    expect(renderToStaticMarkup(<ResumeDrafts workspace={workspace} onOpen={() => {}} />)).toBe('')
  })
})
