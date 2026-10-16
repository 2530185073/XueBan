import { readFileSync, writeFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import Ajv2020 from 'ajv/dist/2020.js'
import addFormats from 'ajv-formats'
import standalone from 'ajv/dist/standalone/index.js'

// Generate shapes from the shared contract. Semantic allOf rules are validated at runtime by the generated validators.
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
const schemas = Object.fromEntries(['states', 'data', 'requests'].map((name) => [name, JSON.parse(readFileSync(new URL(`${name}.schema.json`, source), 'utf8'))]))
const definitions = Object.values(schemas).flatMap((schema) => Object.entries(schema.$defs).map(([key, value]) => `export type ${key} = ${typeOf(value)}\n`))
const types = '// Generated from contracts/phase1; do not edit by hand.\n// Cross-field constraints remain enforced by the source JSON Schemas.\n\n' + definitions.join('\n')

// The served workbench forbids eval under its CSP, so validators are compiled here, not in the browser.
const ajv = new Ajv2020({ strict: false, allErrors: false, code: { source: true, esm: true } })
addFormats(ajv)
for (const schema of Object.values(schemas)) ajv.addSchema(schema)
const exported = {}
for (const kind of ['data', 'requests']) for (const name of Object.keys(schemas[kind].$defs)) exported[`${kind}_${name}`] = `${schemas[kind].$id}#/$defs/${name}`
const imports = new Map()
const defaults = new Set()
const code = standalone(ajv, exported).replace(/require\("([^"\n]+)"\)(\.default)?/g, (_, path, useDefault) => {
  if (!/^ajv(?:-formats)?\/[a-zA-Z0-9_/-]+$/.test(path)) throw new Error('Unexpected generated runtime dependency')
  if (!imports.has(path)) imports.set(path, `runtime${imports.size}`)
  const name = imports.get(path)
  if (useDefault) defaults.add(name)
  return useDefault ? `${name}Default` : name
})
const prefix = [...imports].map(([path, name]) => `import ${name} from '${path}.js'`).concat([...defaults].map((name) => `const ${name}Default = ${name}.default ?? ${name}`)).join('\n')
const validators = '// Generated from contracts/phase1; no runtime compilation or eval.\n' + prefix + '\n' + code + '\n'
const declarations = '// Generated; do not edit.\n' + Object.keys(exported).map((name) => `export declare const ${name}: (value: unknown) => boolean`).join('\n') + '\n'

const outputs = [['contracts.generated.ts', types], ['validators.generated.js', validators], ['validators.generated.d.ts', declarations]]
const target = (file) => fileURLToPath(new URL(`../src/api/${file}`, import.meta.url))
if (process.argv.includes('--check')) {
  const stale = outputs.filter(([file, body]) => { try { return readFileSync(target(file), 'utf8') !== body } catch { return true } })
  if (stale.length) throw new Error(`Shared contracts changed (${stale.map(([file]) => file).join(', ')}): run npm run contracts:generate, review the diff, and test.`)
  console.log('Contract-derived TypeScript shapes and validators match the shared schemas.')
} else { for (const [file, body] of outputs) writeFileSync(target(file), body) }
