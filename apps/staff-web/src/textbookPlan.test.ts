import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import { PROFILE_GRADES, TEXTBOOK_PLAN, textbookPlanText } from './textbookPlan'

describe('textbook plan', () => {
  it('matches the project textbook selection', () => {
    const selection = JSON.parse(readFileSync(new URL('../../../curriculum/selection.json', import.meta.url), 'utf8')) as
      { classes: { grade: number; label: string; subjects: { subject: string; publisher: string }[] }[] }
    const expected = Object.fromEntries(selection.classes.map(c => [c.grade, { label: c.label, subjects: c.subjects.map(s => [s.subject, s.publisher]) }]))
    expect(TEXTBOOK_PLAN).toEqual(expected)
    for (const grade of Object.keys(TEXTBOOK_PLAN)) expect(PROFILE_GRADES.some(([g]) => g === Number(grade))).toBe(true)
  })
  it('summarizes a configured grade and flags an unconfigured one', () => {
    expect(textbookPlanText(5)).toContain('英语·陕西旅游')
    expect(textbookPlanText(9)).toContain('尚未配置')
    expect(textbookPlanText(null)).toBe('')
  })
})
