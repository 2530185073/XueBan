import * as validators from './validators.generated.js'
export function valid(shape: string, value: unknown, request = false): boolean {
  const validate = (validators as Record<string, (value: unknown) => boolean>)[`${request ? 'requests' : 'data'}_${shape}`]
  return Boolean(validate?.(value))
}
