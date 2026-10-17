import { describe, expect, it } from 'vitest'
import { applyChange, checkBatch, checkSelection, composeCrop, MAX_BATCH_BYTES, MAX_PAGE_BYTES, MAX_PAGES, movePage, pageBudget, pageLabel, planEdit, removePage, rotateCrop } from './pages'

const jpeg = (size = 1000) => ({ type: 'image/jpeg', size })
const pages = (...ids: string[]) => ids.map(id => ({ id }))

describe('page limits', () => {
  it('mirrors the server: 4 pages, 4 MiB per page, 8 MiB per batch', () => {
    expect([MAX_PAGES, MAX_PAGE_BYTES, MAX_BATCH_BYTES]).toEqual([4, 4_194_304, 8_388_608])
  })
  it('refuses a selection over the page count before decoding, naming how many still fit', () => {
    expect(() => checkSelection(0, Array.from({ length: 5 }, () => jpeg()))).toThrow(/最多 4 页，这次选了 5 张/)
    expect(() => checkSelection(3, [jpeg(), jpeg()])).toThrow(/已有 3 页.*只能再加 1 张/)
    expect(() => checkSelection(4, [jpeg()])).toThrow(/只能再加 0 张/)
    expect(() => checkSelection(2, [jpeg(), jpeg()])).not.toThrow()
    expect(() => checkSelection(0, [])).toThrow(/没有选到照片/)
  })
  it('names the offending file for unsupported types, empty files and oversize originals', () => {
    expect(() => checkSelection(0, [jpeg(), { type: 'image/gif', size: 10 }])).toThrow(/第 2 张不是 JPG 或 PNG/)
    expect(() => checkSelection(0, [{ type: 'image/png', size: 0 }])).toThrow(/第 1 张是空文件/)
    expect(() => checkSelection(0, [jpeg(25 * 1024 * 1024)])).toThrow(/超过 24 MiB/)
    expect(() => checkSelection(0, [{ type: '', size: 10 }])).not.toThrow()
  })
  it('shares the remaining batch bytes between new pages and caps each at 4 MiB', () => {
    expect(pageBudget(0)).toBe(MAX_PAGE_BYTES)
    expect(pageBudget(0, 4)).toBe(MAX_BATCH_BYTES / 4)
    expect(pageBudget(6_000_000)).toBe(MAX_BATCH_BYTES - 6_000_000)
    expect(() => pageBudget(MAX_BATCH_BYTES)).toThrow(/达到合计 8 MiB 上限/)
  })
  it('checks a whole batch before upload', () => {
    expect(() => checkBatch([4_000_000, 4_000_000])).not.toThrow()
    expect(() => checkBatch([3_000_000, 3_000_000, 3_000_000])).toThrow(/合计 8\.6 MiB，超过 8 MiB/)
    expect(() => checkBatch([1, MAX_PAGE_BYTES + 1])).toThrow(/第 2 页超过 4 MiB/)
    expect(() => checkBatch([1, 1, 1, 1, 1])).toThrow(/最多 4 页/)
  })
})

describe('page order', () => {
  it('moves a page forward/backward and ignores moves past either end', () => {
    expect(movePage(pages('a', 'b', 'c'), 'c', -1).map(p => p.id)).toEqual(['a', 'c', 'b'])
    expect(movePage(pages('a', 'b', 'c'), 'a', 2).map(p => p.id)).toEqual(['b', 'c', 'a'])
    const same = pages('a', 'b'); expect(movePage(same, 'a', -1)).toBe(same); expect(movePage(same, 'missing', 1)).toBe(same)
  })
  it('deletes only the named page and keeps the others in order', () => {
    expect(removePage(pages('a', 'b', 'c'), 'b').map(p => p.id)).toEqual(['a', 'c'])
  })
  it('labels page i / n', () => { expect(pageLabel(1, 3)).toBe('第 2 / 3 页') })
})

describe('turning and cropping', () => {
  it('swaps width and height for a quarter turn and keeps them for a half turn', () => {
    expect(planEdit(300, 200, { rotation: 90, crop: null })).toMatchObject({ width: 200, height: 300, rotatedWidth: 200, rotatedHeight: 300 })
    expect(planEdit(300, 200, { rotation: 270, crop: null })).toMatchObject({ width: 200, height: 300 })
    expect(planEdit(300, 200, { rotation: 180, crop: null })).toMatchObject({ width: 300, height: 200 })
  })
  it('crops in the rotated frame and caps the long edge', () => {
    expect(planEdit(300, 200, { rotation: 90, crop: { x: 0.5, y: 0, width: 0.5, height: 0.5 } })).toMatchObject({ sx: 100, sy: 0, sw: 100, sh: 150, width: 100, height: 150 })
    expect(planEdit(4800, 3600, { rotation: 0, crop: null }, 2400)).toMatchObject({ width: 2400, height: 1800, scale: 0.5 })
  })
  it('turns an existing crop with the page so the same region stays selected', () => {
    const crop = { x: 0.1, y: 0.2, width: 0.3, height: 0.4 }
    const cw = rotateCrop(crop, 1)
    expect(cw.x).toBeCloseTo(0.4); expect(cw.y).toBeCloseTo(0.1); expect(cw.width).toBeCloseTo(0.4); expect(cw.height).toBeCloseTo(0.3)
    const back = rotateCrop(cw, -1)
    for (const k of ['x', 'y', 'width', 'height'] as const) expect(back[k]).toBeCloseTo(crop[k])
    const full = [1, 1, 1, 1].reduce(c => rotateCrop(c, 1), crop)
    for (const k of ['x', 'y', 'width', 'height'] as const) expect(full[k]).toBeCloseTo(crop[k])
  })
  it('composes a crop of the shown page into the original, and resets to the original', () => {
    const once = applyChange({ rotation: 0, crop: null }, { crop: { x: 0.5, y: 0, width: 0.5, height: 1 } })
    const twice = applyChange(once, { crop: { x: 0, y: 0.5, width: 1, height: 0.5 } })
    expect(twice.crop).toEqual(composeCrop(once.crop, { x: 0, y: 0.5, width: 1, height: 0.5 }))
    expect(twice.crop).toEqual({ x: 0.5, y: 0.5, width: 0.5, height: 0.5 })
    expect(applyChange(applyChange(twice, { rotate: 1 }), { rotate: -1 }).rotation).toBe(0)
    expect(applyChange({ rotation: 270, crop: null }, { rotate: 1 }).rotation).toBe(0)
    expect(applyChange(twice, { reset: true })).toEqual({ rotation: 0, crop: null })
    expect(() => applyChange(twice, { crop: { x: 0, y: 0, width: 0.05, height: 0.05 } })).toThrow(/裁切范围太小/)
  })
})
