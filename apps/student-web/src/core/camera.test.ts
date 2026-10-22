import { describe, expect, it } from 'vitest'
import { constrain, fitBox, focusOn, labelTier, lerpCamera, MAX_ZOOM, MIN_ZOOM, panBy, pinch, sameCamera, toScreen, toWorld, zoomAt } from './camera'

const view = { width: 1000, height: 700 }
const box = { x: 0, y: 0, width: 1200, height: 860 }

describe('camera', () => {
  it('screen and world conversions are inverses', () => {
    const camera = { x: 120, y: -40, k: 1.7 }
    const point = { x: 310.5, y: 77.25 }
    const back = toWorld(camera, toScreen(camera, point))
    expect(back.x).toBeCloseTo(point.x); expect(back.y).toBeCloseTo(point.y)
  })

  it('fits the whole box inside the viewport, centred, with padding', () => {
    const camera = fitBox(box, view, 40)
    const a = toScreen(camera, { x: box.x, y: box.y }), b = toScreen(camera, { x: box.width, y: box.height })
    expect(a.x).toBeGreaterThanOrEqual(40 - 0.01); expect(a.y).toBeGreaterThanOrEqual(40 - 0.01)
    expect(b.x).toBeLessThanOrEqual(view.width - 40 + 0.01); expect(b.y).toBeLessThanOrEqual(view.height - 40 + 0.01)
    expect((a.x + b.x) / 2).toBeCloseTo(view.width / 2); expect((a.y + b.y) / 2).toBeCloseTo(view.height / 2)
  })

  it('zooming keeps the world point under the cursor fixed', () => {
    const camera = fitBox(box, view)
    const cursor = { x: 640, y: 210 }
    const before = toWorld(camera, cursor)
    const zoomed = zoomAt(camera, cursor, 1.8)
    const after = toWorld(zoomed, cursor)
    expect(after.x).toBeCloseTo(before.x); expect(after.y).toBeCloseTo(before.y)
    expect(zoomed.k).toBeGreaterThan(camera.k)
  })

  it('never zooms past the limits, in either direction', () => {
    let camera = fitBox(box, view)
    for (let i = 0; i < 40; i++) camera = zoomAt(camera, { x: 500, y: 350 }, 1.4)
    expect(camera.k).toBe(MAX_ZOOM)
    for (let i = 0; i < 80; i++) camera = zoomAt(camera, { x: 500, y: 350 }, 0.7)
    expect(camera.k).toBe(MIN_ZOOM)
  })

  it('cannot be dragged so far that the map is lost', () => {
    const camera = fitBox(box, view)
    for (const [dx, dy] of [[9000, 0], [-9000, 0], [0, 9000], [0, -9000], [-9000, -9000]]) {
      const flung = constrain(panBy(camera, dx, dy), box, view, 120)
      const left = box.x * flung.k + flung.x, right = (box.x + box.width) * flung.k + flung.x
      const top = box.y * flung.k + flung.y, bottom = (box.y + box.height) * flung.k + flung.y
      expect(right).toBeGreaterThanOrEqual(120 - 0.01); expect(left).toBeLessThanOrEqual(view.width - 120 + 0.01)
      expect(bottom).toBeGreaterThanOrEqual(120 - 0.01); expect(top).toBeLessThanOrEqual(view.height - 120 + 0.01)
    }
  })

  it('a camera already in range is left untouched', () => {
    const camera = fitBox(box, view)
    expect(constrain(camera, box, view)).toEqual(camera)
  })

  it('focusing centres the point in the space left of a drawer', () => {
    const camera = focusOn(fitBox(box, view), { x: 800, y: 300 }, view, 360)
    const screen = toScreen(camera, { x: 800, y: 300 })
    expect(screen.x).toBeCloseTo((view.width - 360) / 2); expect(screen.y).toBeCloseTo(view.height / 2)
    expect(camera.k).toBeGreaterThanOrEqual(1.35)
  })

  it('the glide starts and ends exactly on its cameras and is monotonic in scale', () => {
    const a = { x: 0, y: 0, k: 0.8 }, b = { x: -400, y: -150, k: 2.4 }
    expect(lerpCamera(a, b, 0)).toEqual(a)
    const end = lerpCamera(a, b, 1)
    expect(end.x).toBeCloseTo(b.x); expect(end.y).toBeCloseTo(b.y); expect(end.k).toBeCloseTo(b.k)
    let last = a.k
    for (let t = 0.05; t <= 1; t += 0.05) { const k = lerpCamera(a, b, t).k; expect(k).toBeGreaterThanOrEqual(last - 1e-9); last = k }
  })

  it('label detail depends on zoom: calm when out, informative when in', () => {
    expect(labelTier(0.6)).toBe('clusters'); expect(labelTier(1.2)).toBe('attention'); expect(labelTier(2.4)).toBe('all')
  })

  it('pinch reports the zoom factor and where the midpoint moved', () => {
    const prev: [{ x: number; y: number }, { x: number; y: number }] = [{ x: 100, y: 100 }, { x: 200, y: 100 }]
    const next: [{ x: number; y: number }, { x: number; y: number }] = [{ x: 90, y: 110 }, { x: 290, y: 110 }]
    const g = pinch(prev, next)
    expect(g.factor).toBeCloseTo(2); expect(g.from).toEqual({ x: 150, y: 100 }); expect(g.to).toEqual({ x: 190, y: 110 })
  })

  it('two fingers on the same spot do not divide by zero', () => {
    const same: [{ x: number; y: number }, { x: number; y: number }] = [{ x: 5, y: 5 }, { x: 5, y: 5 }]
    expect(Number.isFinite(pinch(same, same).factor)).toBe(true)
  })

  it('sameCamera tolerates float noise', () => {
    expect(sameCamera({ x: 1, y: 2, k: 1 }, { x: 1.001, y: 2, k: 1.0000001 })).toBe(true)
    expect(sameCamera({ x: 1, y: 2, k: 1 }, { x: 3, y: 2, k: 1 })).toBe(false)
  })
})
