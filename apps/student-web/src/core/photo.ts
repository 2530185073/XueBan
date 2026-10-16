import type { NormalizedPhoto } from './types'
import { StudentError } from './errors'
const invalid = () => new StudentError('invalid_photo', 422, '请选择清晰的 JPEG、PNG 或 WebP 图片，原图不能超过 2400 万像素。')
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
export async function normalizePhoto(file: Blob): Promise<NormalizedPhoto> {
  if (!file.size || file.size > 24 * 1024 * 1024) throw invalid()
  imageDimensions(new Uint8Array(await file.arrayBuffer())) // Bound decoded allocation before browser decode.
  let bitmap: ImageBitmap
  try { bitmap = await createImageBitmap(file, { imageOrientation: 'from-image' }) } catch { throw invalid() }
  try {
    let { width, height } = bitmap
    if (width * height > 24_000_000) throw invalid()
    const canvas = document.createElement('canvas'); const context = canvas.getContext('2d'); if (!context) throw invalid()
    for (let step = 0; step < 8; step++) {
      canvas.width = width; canvas.height = height
      context.fillStyle = 'white'; context.fillRect(0, 0, width, height); context.drawImage(bitmap, 0, 0, width, height)
      const blob = await new Promise<Blob>((resolve, reject) => canvas.toBlob(value => value ? resolve(value) : reject(invalid()), 'image/jpeg', step ? 0.85 : 0.94))
      if (blob.size <= 4_194_304) {
        const hash = await crypto.subtle.digest('SHA-256', await blob.arrayBuffer())
        return { blob, width, height, sha256: Array.from(new Uint8Array(hash), n => n.toString(16).padStart(2, '0')).join(''), mime: 'image/jpeg', size: blob.size }
      }
      width = Math.max(1, Math.floor(width * 0.8)); height = Math.max(1, Math.floor(height * 0.8))
    }
    throw invalid()
  } finally { bitmap.close() }
}
