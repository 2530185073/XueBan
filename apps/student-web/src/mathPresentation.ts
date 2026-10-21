/** Read-only typography, never a parser for grading or normalization. */
export type MathPart = { kind: 'text'; text: string } | { kind: 'exponent'; text: string; source: string }
export type MathPresentation = { kind: 'fraction'; numerator: string; denominator: string; source: string } | { kind: 'powers'; parts: MathPart[]; source: string }
const MAX_TEXT = 4000, MAX_EXPONENTS = 32, MAX_GROUP = 80
const closes: Record<string, string> = { '(': ')', '{': '}', '[': ']' }
const isBoundary = (tail: string, grouped: boolean) => {
  const rest = tail.trimStart(), next = rest[0]
  // Do not guess an ungrouped exponent's endpoint across comma/ratio notation.
  if (!grouped && /^[,，:：]/u.test(rest)) return false
  return !next || grouped && /[+\-−/!！]/u.test(next) || /[\u3400-\u9fff，。；：、？=<>≤≥≠×÷*·,;:?\])}]/u.test(next)
}
function balanced(text: string) {
  const stack: string[] = []
  for (const char of text) {
    if (char in closes) { stack.push(closes[char]); if (stack.length > 8) return false }
    else if (char === ')' || char === '}' || char === ']') { if (stack.pop() !== char) return false }
  }
  return !stack.length
}
/** Any unsupported caret leaves the WHOLE field unchanged, including earlier powers. */
export function mathPresentation(text: string): MathPresentation | null {
  if (!text || text.length > MAX_TEXT || /[$\\\u0000-\u0008\u000b\u000c\u000e-\u001f]/u.test(text)) return null
  const fraction = /^([+\-−]?\d{1,12})\/([+\-−]?\d{1,12})$/.exec(text.trim())
  if (fraction) return { kind: 'fraction', numerator: fraction[1], denominator: fraction[2], source: text }
  if (!text.includes('^') || !balanced(text)) return null
  const parts: MathPart[] = []; let start = 0, cursor = 0, count = 0, previousEnd = -1
  while ((cursor = text.indexOf('^', cursor)) !== -1) {
    if (++count > MAX_EXPONENTS || cursor === previousEnd || !/[A-Za-z0-9)]/.test(text[cursor - 1] ?? '')) return null
    let end: number, exponent: string
    const first = text[cursor + 1]
    if (first === '(' || first === '{') {
      const stack = [closes[first]]; let i = cursor + 2
      for (; i < text.length && stack.length; i++) {
        const char = text[i]
        if (char in closes) { stack.push(closes[char]); if (stack.length > 4) return null }
        else if (char === ')' || char === '}' || char === ']') { if (stack.pop() !== char) return null }
      }
      if (stack.length) return null
      end = i; exponent = text.slice(cursor + 2, end - 1)
      // Explicit grouping supplies scope; no nested power, command or prose.
      if (!exponent.trim() || exponent.length > MAX_GROUP || !/^[A-Za-z0-9()+\-−*/. =×÷{}]+$/.test(exponent)) return null
    } else {
      const number = /^[+\-−]?\d{1,6}/.exec(text.slice(cursor + 1))
      if (!number) return null
      exponent = number[0]; end = cursor + 1 + exponent.length
    }
    if (!isBoundary(text.slice(end), first === '(' || first === '{')) return null
    parts.push({ kind: 'text', text: text.slice(start, cursor) }, { kind: 'exponent', text: exponent, source: text.slice(cursor, end) })
    previousEnd = end; start = end; cursor = end
  }
  if (!count) return null
  parts.push({ kind: 'text', text: text.slice(start) })
  return { kind: 'powers', parts, source: text }
}
