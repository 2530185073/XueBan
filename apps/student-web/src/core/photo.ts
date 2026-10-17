import type { NormalizedPhoto } from './types'
import { StudentError } from './errors'
import { MAX_PAGE_BYTES, MAX_SOURCE_BYTES, mib, planEdit, type PageEdit } from './pages'
const invalid = () => new StudentError('invalid_photo', 422, '这张照片打不开。请选择清晰的 JPG 或 PNG 图片，原图不超过 2400 万像素、24 MiB。')
export function imageDimensions(bytes: Uint8Array): { width: number; height: number } {
  const v = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength); let width = 0, height = 0
  const ascii = (start: number, length: number) => String.fromCharCode(...bytes.slice(start, start + length))
  try {
    if (bytes[0] === 137 && ascii(1, 3) === 'PNG' && ascii(12, 4) === 'IHDR') { width = v.getUint32(16); height = v.getUint32(20) }
    else if (ascii(0, 4) === 'RIFF' && ascii(8, 4) === 'WEBP') {
      const kind = ascii(12, 4)
      if (kind === 'VP8X') { width = 1 + bytes[24] + (bytes[25] << 8) + (bytes[26] << 16); height = 1 + bytes[27] + (bytes[28] << 8) + (bytes[29] << 16) }
      else if (kind === 'VP8L' && bytes[20] === 47) { const n = v.getUint32(21, true); width = (n & 0x3fff) + 1; height = ((n >>> 14) & 0x3fff) + 1 }
      else if (kind === 'VP8 ' && bytes[23] === 157 && bytes[24] === 1 && bytes[25] === 42) { width = v.getUint16(26, true) & 0x3fff; height = v.getUint16(28, true) & 0x3fff }
    } else if (bytes[0] === 255 && bytes[1] === 216) {
      let i = 2
      while (i + 4 < bytes.length) {
        if (bytes[i++] !== 255) throw invalid()
        while (bytes[i] === 255) i++
        const marker = bytes[i++]; if (marker === 218 || marker === 217) break
        if (marker === 1 || marker >= 208 && marker <= 215) continue
        const length = v.getUint16(i); if (length < 2) throw invalid()
        if ([192, 193, 194, 195, 197, 198, 199, 201, 202, 203, 205, 206, 207].includes(marker)) { height = v.getUint16(i + 3); width = v.getUint16(i + 5); break }
        i += length
      }
    }
  } catch { throw invalid() }
  if (!Number.isInteger(width) || !Number.isInteger(height) || width < 1 || height < 1 || width * height > 24_000_000) throw invalid()
  return { width, height }
}
/** Long edge of an uploaded page; enough for handwriting and keeps four pages well inside the batch total. */
export const MAX_EDGE = 2400
const MIN_EDGE = 900
const tooLarge = (maxBytes: number) => new StudentError('page_too_large', 422, `这张照片压缩后仍超过 ${mib(maxBytes)}，可能是画面太复杂或太大。请靠近作业重新拍一张，或裁掉多余部分。`)
/** Draws at decreasing size until the JPEG fits `maxBytes`; refuses rather than shrinking text below a readable size. */
async function encode(width: number, height: number, draw: (context: CanvasRenderingContext2D, scale: number) => void, maxBytes: number): Promise<NormalizedPhoto> {
  const canvas = document.createElement('canvas'); const context = canvas.getContext('2d'); if (!context) throw invalid()
  for (let step = 0, scale = 1; Math.max(width, height) * scale >= MIN_EDGE || step === 0; step++, scale *= 0.82) {
    const w = Math.max(1, Math.round(width * scale)), h = Math.max(1, Math.round(height * scale))
    canvas.width = w; canvas.height = h
    context.setTransform(1, 0, 0, 1, 0, 0); context.fillStyle = 'white'; context.fillRect(0, 0, w, h); draw(context, w / width)
    const blob = await new Promise<Blob>((resolve, reject) => canvas.toBlob(value => value ? resolve(value) : reject(invalid()), 'image/jpeg', step ? 0.85 : 0.9))
    if (blob.size <= maxBytes) {
      const hash = await crypto.subtle.digest('SHA-256', await blob.arrayBuffer())
      return { blob, width: w, height: h, sha256: Array.from(new Uint8Array(hash), n => n.toString(16).padStart(2, '0')).join(''), mime: 'image/jpeg', size: blob.size }
    }
  }
  throw tooLarge(maxBytes)
}
export async function normalizePhoto(file: Blob, maxBytes = MAX_PAGE_BYTES): Promise<NormalizedPhoto> {
  if (!file.size || file.size > MAX_SOURCE_BYTES) throw invalid()
  imageDimensions(new Uint8Array(await file.arrayBuffer())) // Bound decoded allocation before browser decode.
  let bitmap: ImageBitmap
  try { bitmap = await createImageBitmap(file, { imageOrientation: 'from-image' }) } catch { throw invalid() }
  try {
    if (bitmap.width * bitmap.height > 24_000_000) throw invalid()
    const plan = planEdit(bitmap.width, bitmap.height, { rotation: 0, crop: null }, MAX_EDGE)
    return await encode(plan.width, plan.height, (context, scale) => context.drawImage(bitmap, 0, 0, bitmap.width * plan.scale * scale, bitmap.height * plan.scale * scale), maxBytes)
  } finally { bitmap.close() }
}
/** Re-renders a kept local original turned and cropped, so repeated edits never stack JPEG losses. */
export async function editPhoto(source: NormalizedPhoto, edit: PageEdit, maxBytes = MAX_PAGE_BYTES): Promise<NormalizedPhoto> {
  let bitmap: ImageBitmap
  try { bitmap = await createImageBitmap(source.blob) } catch { throw new StudentError('photo_unreadable', 0, '这一页的照片在本机读取不到了，请删除这一页后重新拍照或选择。') }
  try {
    const plan = planEdit(bitmap.width, bitmap.height, edit, MAX_EDGE)
    return await encode(plan.width, plan.height, (context, scale) => {
      const s = plan.scale * scale
      context.scale(s, s); context.translate(-plan.sx, -plan.sy)
      context.translate(plan.rotatedWidth / 2, plan.rotatedHeight / 2); context.rotate(edit.rotation * Math.PI / 180)
      context.drawImage(bitmap, -bitmap.width / 2, -bitmap.height / 2)
    }, maxBytes)
  } finally { bitmap.close() }
}
