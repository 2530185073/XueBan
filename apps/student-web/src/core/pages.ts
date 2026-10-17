import { StudentError } from './errors'

/** Mirrors StartRecognition.asset_ids maxItems, CreateUpload.byte_size and the server's per-batch total. */
export const MAX_PAGES = 4
export const MAX_PAGE_BYTES = 4_194_304
export const MAX_BATCH_BYTES = 8_388_608
/** Raw file accepted for local re-encoding; only the re-encoded JPEG is uploaded. */
export const MAX_SOURCE_BYTES = 24 * 1024 * 1024
export const ACCEPTED_TYPES = ['image/jpeg', 'image/png'] as const
export const LIMITS_TEXT = `最多 ${MAX_PAGES} 页 · 每页不超过 4 MiB · 合计不超过 8 MiB`

export type Rotation = 0 | 90 | 180 | 270
/** Fractions (0–1) of the rotated original. */
export interface Crop { x: number; y: number; width: number; height: number }
export interface PageEdit { rotation: Rotation; crop: Crop | null }
export interface EditChange { rotate?: 1 | -1; crop?: Crop; reset?: true }

export const mib = (bytes: number) => `${(bytes / 1_048_576).toFixed(1)} MiB`
const nth = (index: number) => `第 ${index + 1} 张`

/** Count and type checks for a new selection, before any decoding or saving. */
export function checkSelection(existing: number, files: { type: string; size: number }[]) {
  if (!files.length) throw new StudentError('no_photo', 422, '没有选到照片，请重新选择。')
  if (existing + files.length > MAX_PAGES) throw new StudentError('too_many_pages', 422, existing ? `这份作业已有 ${existing} 页，最多 ${MAX_PAGES} 页，这次只能再加 ${Math.max(0, MAX_PAGES - existing)} 张。请少选几张后重试。` : `一次最多 ${MAX_PAGES} 页，这次选了 ${files.length} 张。请少选几张，或分成两份作业提交。`)
  files.forEach((file, index) => {
    if (file.type && !(ACCEPTED_TYPES as readonly string[]).includes(file.type)) throw new StudentError('unsupported_photo', 422, `${nth(index)}不是 JPG 或 PNG 图片，请换一张后重新选择。`)
    if (!file.size) throw new StudentError('empty_photo', 422, `${nth(index)}是空文件，请重新拍照或选择。`)
    if (file.size > MAX_SOURCE_BYTES) throw new StudentError('photo_too_large', 422, `${nth(index)}原图有 ${mib(file.size)}，超过 24 MiB，学伴无法压缩。请用相机重新拍一张。`)
  })
}
/** Per-page byte target for `count` new pages so the batch stays within the total. */
export function pageBudget(usedBytes: number, count = 1) {
  const left = MAX_BATCH_BYTES - usedBytes
  if (left <= 0) throw new StudentError('batch_too_large', 422, `这份作业的照片已有 ${mib(usedBytes)}，达到合计 8 MiB 上限。请删掉一页后再加。`)
  return Math.min(MAX_PAGE_BYTES, Math.floor(left / count))
}
export function checkBatch(sizes: number[]) {
  if (sizes.length > MAX_PAGES) throw new StudentError('too_many_pages', 422, `一份作业最多 ${MAX_PAGES} 页，请删掉多余的页。`)
  const total = sizes.reduce((sum, size) => sum + size, 0), big = sizes.findIndex(size => size > MAX_PAGE_BYTES)
  if (big >= 0) throw new StudentError('page_too_large', 422, `第 ${big + 1} 页超过 4 MiB，请重拍这一页。`)
  if (total > MAX_BATCH_BYTES) throw new StudentError('batch_too_large', 422, `这几页合计 ${mib(total)}，超过 8 MiB。请删掉一页或裁掉多余部分。`)
}
export function movePage<T extends { id: string }>(pages: T[], id: string, delta: number): T[] {
  const from = pages.findIndex(page => page.id === id), to = from + delta
  if (from < 0 || to < 0 || to >= pages.length) return pages
  const next = [...pages]; const [page] = next.splice(from, 1); next.splice(to, 0, page); return next
}
export const removePage = <T extends { id: string }>(pages: T[], id: string): T[] => pages.filter(page => page.id !== id)

const turn = (rotation: Rotation, delta: 1 | -1) => (((rotation + delta * 90) % 360 + 360) % 360) as Rotation
/** The same region, described in the frame turned by 90° clockwise (1) or counter-clockwise (-1). */
export function rotateCrop(crop: Crop, delta: 1 | -1): Crop {
  return delta === 1 ? { x: 1 - crop.y - crop.height, y: crop.x, width: crop.height, height: crop.width } : { x: crop.y, y: 1 - crop.x - crop.width, width: crop.height, height: crop.width }
}
/** A crop of the currently shown page, expressed in the rotated original. */
export function composeCrop(outer: Crop | null, inner: Crop): Crop {
  const base = outer ?? { x: 0, y: 0, width: 1, height: 1 }
  return { x: base.x + inner.x * base.width, y: base.y + inner.y * base.height, width: inner.width * base.width, height: inner.height * base.height }
}
export function validCrop(crop: Crop) {
  return [crop.x, crop.y, crop.width, crop.height].every(Number.isFinite) && crop.x >= 0 && crop.y >= 0 && crop.width >= 0.05 && crop.height >= 0.05 && crop.x + crop.width <= 1.000001 && crop.y + crop.height <= 1.000001
}
export const isIdentity = (edit: PageEdit) => edit.rotation === 0 && !edit.crop
export function applyChange(edit: PageEdit, change: EditChange): PageEdit {
  if (change.reset) return { rotation: 0, crop: null }
  if (change.rotate) return { rotation: turn(edit.rotation, change.rotate), crop: edit.crop && rotateCrop(edit.crop, change.rotate) }
  if (change.crop) {
    const crop = composeCrop(edit.crop, change.crop)
    if (!validCrop(crop)) throw new StudentError('invalid_crop', 422, '裁切范围太小了，请至少保留题目和作答。')
    return { rotation: edit.rotation, crop: crop.width > 0.999 && crop.height > 0.999 ? null : crop }
  }
  return edit
}
/** Canvas geometry for drawing `width`×`height` turned by `rotation`, cropped, then scaled so the long edge stays within `maxEdge`. */
export function planEdit(width: number, height: number, edit: PageEdit, maxEdge = Infinity) {
  const turned = edit.rotation % 180 !== 0, rotatedWidth = turned ? height : width, rotatedHeight = turned ? width : height
  const crop = edit.crop ?? { x: 0, y: 0, width: 1, height: 1 }
  const sx = Math.round(crop.x * rotatedWidth), sy = Math.round(crop.y * rotatedHeight)
  const sw = Math.max(1, Math.min(rotatedWidth - sx, Math.round(crop.width * rotatedWidth))), sh = Math.max(1, Math.min(rotatedHeight - sy, Math.round(crop.height * rotatedHeight)))
  const scale = Math.min(1, maxEdge / Math.max(sw, sh))
  return { rotatedWidth, rotatedHeight, sx, sy, sw, sh, scale, width: Math.max(1, Math.round(sw * scale)), height: Math.max(1, Math.round(sh * scale)) }
}
export const pageLabel = (index: number, count: number) => `第 ${index + 1} / ${count} 页`
