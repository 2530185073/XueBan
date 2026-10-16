import type { Region } from './api/contracts.generated'

export interface Size { width: number; height: number }
export interface Box { left: number; top: number; width: number; height: number }
export interface Point { x: number; y: number }

/** Smallest side, in original pixels, that still counts as a deliberate rectangle. */
export const MIN_REGION_SIDE = 8

const clamp = (value: number, low: number, high: number) => Math.min(high, Math.max(low, value))

/** Map a pointer position on the displayed image to original image pixels, clamped to the image. */
export function toOriginal(clientX: number, clientY: number, box: Box, size: Size): Point {
  if (box.width <= 0 || box.height <= 0) return { x: 0, y: 0 }
  return {
    x: clamp(((clientX - box.left) / box.width) * size.width, 0, size.width),
    y: clamp(((clientY - box.top) / box.height) * size.height, 0, size.height),
  }
}

/** Integer rectangle spanning two original-pixel points; null when too small to be intended. */
export function regionFromDrag(assetId: string, start: Point, end: Point, size: Size): Region | null {
  const left = Math.floor(clamp(Math.min(start.x, end.x), 0, size.width))
  const top = Math.floor(clamp(Math.min(start.y, end.y), 0, size.height))
  const right = Math.ceil(clamp(Math.max(start.x, end.x), 0, size.width))
  const bottom = Math.ceil(clamp(Math.max(start.y, end.y), 0, size.height))
  if (right - left < MIN_REGION_SIDE || bottom - top < MIN_REGION_SIDE) return null
  return { asset_id: assetId, x: left, y: top, width: right - left, height: bottom - top, coordinate_space: 'original_pixels' }
}

export function insideImage(region: Region, size: Size): boolean {
  return region.x >= 0 && region.y >= 0 && region.width > 0 && region.height > 0 && region.x + region.width <= size.width && region.y + region.height <= size.height
}

/** The displayed photo must keep the recorded aspect ratio, otherwise drawn pixels would land elsewhere (e.g. EXIF rotation). */
export function aspectMatches(natural: Size, recorded: Size, tolerance = 0.01): boolean {
  if (!natural.width || !natural.height || !recorded.width || !recorded.height) return false
  return Math.abs(natural.width / natural.height - recorded.width / recorded.height) / (recorded.width / recorded.height) <= tolerance
}
