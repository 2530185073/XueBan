import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

const css = readFileSync(new URL('./styles.css', import.meta.url), 'utf8')
const rules = new Map<string, string>()
for (const [, selector, declarations] of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
  const name = selector.replace(/\/\*[\s\S]*?\*\//g, '').trim()
  rules.set(name, `${rules.get(name) ?? ''};${declarations}`)
}
const layer = (selector: string) => Number(rules.get(selector)?.match(/(?:^|;)z-index:(\d+)/)?.[1])
const openHeader = '.app-shell .topbar:has(.account-menu)'

describe('account menu stacking', () => {
  it('raises the blurred header above page controls, not just the nested menu', () => {
    const header = rules.get('.app-shell .topbar')!
    expect(header).toContain('position:relative')
    expect(header).toContain('backdrop-filter:blur(16px)')
    expect(layer(openHeader)).toBeGreaterThan(layer('.activity-indicator'))
    expect(layer(openHeader)).toBeGreaterThan(layer('.app-shell .next-capture-wrap'))
    expect(header).not.toMatch(/overflow(?:-x|-y)?:(hidden|clip|auto|scroll)/)
  })

  it('keeps the menu anchored without permanently raising the header above photo dialogs', () => {
    expect(rules.get('.app-shell .account')).toContain('position:relative')
    expect(rules.get('.app-shell .account-menu')).toContain('position:absolute')
    expect(rules.get('.app-shell .topbar')).not.toContain('z-index:')
    expect(layer(openHeader)).toBeLessThan(layer('.image-dialog'))
  })
})

describe('frameless top navigation', () => {
  it('removes the surrounding frame and its vertical padding at every breakpoint', () => {
    const nav = rules.get('.app-shell .topbar nav')!
    expect(nav).toContain('border:0')
    expect(nav).toContain('background:transparent')
    expect(nav).toContain('align-items:center')
    expect(nav).toContain('height:44px')
    expect(nav.match(/(?:^|;)padding:[^;]+/g)?.every(value => value === ';padding:0')).toBe(true)
  })

  it('centres each label in a full touch target without an off-centre active dot', () => {
    const button = rules.get('.app-shell .topbar nav button')!
    for (const declaration of ['display:inline-flex', 'align-items:center', 'justify-content:center', 'align-self:center', 'height:44px', 'min-height:44px', 'line-height:20px', 'padding:0 16px']) expect(button).toContain(declaration)
    const nav = rules.get('.app-shell .topbar nav')!
    expect(nav).toContain('display:grid')
    expect(nav).toContain('grid-auto-columns:minmax(0,1fr)')
    expect(nav).toContain('gap:8px')
    expect(button).toContain('width:100%')
    expect(rules.get('.app-shell .topbar nav button.active:after')).toContain('content:none')
    expect(rules.get('.app-shell .topbar nav button:focus-visible')).toContain('outline-offset:-2px')
  })
})
