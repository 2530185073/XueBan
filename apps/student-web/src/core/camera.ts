/**
 * A 2D camera for the galaxy view. Pure maths, no DOM: the component owns the pointer events and applies the result
 * as one SVG transform, so panning and zooming never re-render thousands of nodes.
 *
 * World space is the layout's coordinate system. A camera is `{ x, y, k }`: the screen point of the world origin and the
 * scale. screen = world * k + (x, y).
 */
export interface Camera { x: number; y: number; k: number }
export interface Size { width: number; height: number }
export interface Box { x: number; y: number; width: number; height: number }

export const MIN_ZOOM = 0.55
export const MAX_ZOOM = 3.2

const clamp = (value: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, value))

export function toScreen(camera: Camera, point: { x: number; y: number }) {
  return { x: point.x * camera.k + camera.x, y: point.y * camera.k + camera.y }
}

export function toWorld(camera: Camera, point: { x: number; y: number }) {
  return { x: (point.x - camera.x) / camera.k, y: (point.y - camera.y) / camera.k }
}

/** Fit a world box inside a viewport with padding, centred. */
export function fitBox(box: Box, view: Size, padding = 40): Camera {
  const k = clamp(Math.min((view.width - padding * 2) / box.width, (view.height - padding * 2) / box.height), MIN_ZOOM, MAX_ZOOM)
  return { k, x: (view.width - box.width * k) / 2 - box.x * k, y: (view.height - box.height * k) / 2 - box.y * k }
}

/** Zoom about a screen point so the world point under the cursor/fingers stays put. */
export function zoomAt(camera: Camera, screen: { x: number; y: number }, factor: number): Camera {
  const k = clamp(camera.k * factor, MIN_ZOOM, MAX_ZOOM)
  const world = toWorld(camera, screen)
  return { k, x: screen.x - world.x * k, y: screen.y - world.y * k }
}

export function panBy(camera: Camera, dx: number, dy: number): Camera {
  return { ...camera, x: camera.x + dx, y: camera.y + dy }
}

/**
 * Keep the content from being dragged out of reach: at least `keep` pixels of the world box must stay on screen on each axis.
 * Without this a fling can lose the whole map and the student has no way back except a reset.
 */
export function constrain(camera: Camera, bounds: Box, view: Size, keep = 120): Camera {
  const left = bounds.x * camera.k + camera.x, right = (bounds.x + bounds.width) * camera.k + camera.x
  const top = bounds.y * camera.k + camera.y, bottom = (bounds.y + bounds.height) * camera.k + camera.y
  let { x, y } = camera
  if (right < keep) x += keep - right
  if (left > view.width - keep) x -= left - (view.width - keep)
  if (bottom < keep) y += keep - bottom
  if (top > view.height - keep) y -= top - (view.height - keep)
  return { ...camera, x, y }
}

/** Centre a world point in the part of the viewport not covered by a drawer on the right. */
export function focusOn(camera: Camera, point: { x: number; y: number }, view: Size, coveredRight = 0, zoom?: number): Camera {
  const k = clamp(zoom ?? Math.max(camera.k, 1.35), MIN_ZOOM, MAX_ZOOM)
  const visibleWidth = Math.max(1, view.width - coveredRight)
  return { k, x: visibleWidth / 2 - point.x * k, y: view.height / 2 - point.y * k }
}

/** Ease between two cameras; interpolating in log-scale keeps zoom speed even in and out. */
export function lerpCamera(a: Camera, b: Camera, t: number): Camera {
  const e = t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2
  const k = Math.exp(Math.log(a.k) + (Math.log(b.k) - Math.log(a.k)) * e)
  // Interpolate the world point at the viewport centre so the move reads as a glide, not a slide.
  return { k, x: a.x + (b.x - a.x) * e, y: a.y + (b.y - a.y) * e }
}

export function sameCamera(a: Camera, b: Camera, epsilon = 0.01) {
  return Math.abs(a.x - b.x) < epsilon && Math.abs(a.y - b.y) < epsilon && Math.abs(a.k - b.k) < epsilon / 10
}

/** Detail level: which labels may show at this zoom, so a zoomed-out map stays calm and a zoomed-in one is informative. */
export function labelTier(k: number): 'clusters' | 'attention' | 'all' {
  return k < 0.95 ? 'clusters' : k < 1.7 ? 'attention' : 'all'
}

/** Two-finger gesture maths: zoom factor and the midpoint movement between two frames. */
export function pinch(prev: [{ x: number; y: number }, { x: number; y: number }], next: [{ x: number; y: number }, { x: number; y: number }]) {
  const dist = (p: typeof prev) => Math.hypot(p[0].x - p[1].x, p[0].y - p[1].y) || 1
  const mid = (p: typeof prev) => ({ x: (p[0].x + p[1].x) / 2, y: (p[0].y + p[1].y) / 2 })
  return { factor: dist(next) / dist(prev), from: mid(prev), to: mid(next) }
}
