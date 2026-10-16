import * as validators from './validators.generated.js'

const table = validators as Record<string, (value: unknown) => boolean>

export function matchesContract(kind: 'data' | 'requests', name: string, value: unknown): boolean {
  const validate = table[`${kind}_${name}`]
  if (!validate) throw new Error(`Unknown contract shape: ${name}`)
  return Boolean(validate(value))
}
