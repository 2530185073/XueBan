import { readFileSync, writeFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

// Generate shapes from the shared contract. Semantic allOf rules are validated at runtime by Ajv.
const source = new URL('../../../contracts/phase1/', import.meta.url)
const typeOf = (schema) => {
  if (schema.$ref) return schema.$ref.split('/').at(-1)
  if ('const' in schema) return JSON.stringify(schema.const)
  if (schema.enum) return schema.enum.map((value) => JSON.stringify(value)).join(' | ')
  if (schema.anyOf || schema.oneOf) return (schema.anyOf ?? schema.oneOf).map(typeOf).join(' | ')
  if (schema.type === 'array') return `Array<${typeOf(schema.items)}>`
  if (schema.type === 'object') return `{ ${Object.entries(schema.properties ?? {}).map(([name, value]) => `${JSON.stringify(name)}${schema.required?.includes(name) ? '' : '?'}: ${typeOf(value)}`).join('; ')} }`
  if (schema.type === 'integer' || schema.type === 'number') return 'number'
  if (schema.type === 'string' || schema.type === 'boolean' || schema.type === 'null') return schema.type
  return 'unknown'
}
const definitions = ['states.schema.json', 'data.schema.json', 'requests.schema.json'].flatMap((name) => {
  const schema = JSON.parse(readFileSync(new URL(name, source), 'utf8'))
  return Object.entries(schema.$defs).map(([key, value]) => `export type ${key} = ${typeOf(value)}\n`)
})
const output = '// Generated from contracts/phase1; do not edit by hand.\n// Cross-field constraints remain enforced by the source JSON Schemas.\n\n' + definitions.join('\n')
const target = fileURLToPath(new URL('../src/api/contracts.generated.ts', import.meta.url))
if (process.argv.includes('--check')) {
  if (readFileSync(target, 'utf8') !== output) throw new Error('Shared contracts changed: run npm run contracts:generate, review the diff, and test.')
  console.log('Contract-derived TypeScript shapes match the shared schemas.')
} else { writeFileSync(target, output) }
