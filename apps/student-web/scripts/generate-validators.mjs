import { readFile, writeFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import { resolve, dirname } from 'node:path'
import Ajv2020 from 'ajv/dist/2020.js'
import addFormats from 'ajv-formats'
import standalone from 'ajv/dist/standalone/index.js'
const here = dirname(fileURLToPath(import.meta.url))
const schemas = {}
for (const name of ['states', 'data', 'requests']) schemas[name] = JSON.parse(await readFile(resolve(here, '../../../contracts/phase1', `${name}.schema.json`), 'utf8'))
const ajv = new Ajv2020({ strict: false, allErrors: false, code: { source: true, esm: true } })
addFormats(ajv)
for (const schema of Object.values(schemas)) ajv.addSchema(schema)
const exports = {}
for (const kind of ['data', 'requests']) for (const name of Object.keys(schemas[kind].$defs)) exports[`${kind}_${name}`] = `${schemas[kind].$id}#/$defs/${name}`
let code = standalone(ajv, exports)
const imports = new Map()
const defaults = new Set()
code = code.replace(/require\("([^"\n]+)"\)(\.default)?/g, (_, path, useDefault) => {
  if (!/^ajv(?:-formats)?\/[a-zA-Z0-9_/-]+$/.test(path)) throw new Error('Unexpected generated runtime dependency')
  if (!imports.has(path)) imports.set(path, `runtime${imports.size}`)
  const name = imports.get(path)
  if (useDefault) defaults.add(name)
  return useDefault ? `${name}Default` : name
})
const prefix = [...imports].map(([path, name]) => `import ${name} from '${path}.js'`).concat([...defaults].map(name => `const ${name}Default = ${name}.default ?? ${name}`)).join('\n')
const output = '// Generated from contracts/phase1; no runtime compilation or eval.\n' + prefix + '\n' + code + '\n'
const declarations = '// Generated; do not edit.\n' + Object.keys(exports).map(name => `export declare const ${name}: (value: unknown) => boolean`).join('\n') + '\n'
for (const [file, body] of [['validators.generated.js', output], ['validators.generated.d.ts', declarations]]) {
  const target = resolve(here, '../src/core', file)
  if (process.argv.includes('--check')) {
    if (await readFile(target, 'utf8').catch(() => '') !== body) { console.error(`Stale ${file}: run npm run contracts:generate`); process.exitCode = 1 }
  } else await writeFile(target, body)
}
