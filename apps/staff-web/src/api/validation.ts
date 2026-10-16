import Ajv2020 from 'ajv/dist/2020'
import addFormats from 'ajv-formats'
import data from '../../../../contracts/phase1/data.schema.json'
import states from '../../../../contracts/phase1/states.schema.json'
import requests from '../../../../contracts/phase1/requests.schema.json'

const validator = new Ajv2020({ allErrors: true, strict: false })
addFormats(validator)
validator.addSchema(states).addSchema(data).addSchema(requests)

export function matchesContract(kind: 'data' | 'requests', name: string, value: unknown): boolean {
  const id = kind === 'data' ? data.$id : requests.$id
  const validate = validator.getSchema(`${id}#/$defs/${name}`)
  if (!validate) throw new Error(`Unknown contract shape: ${name}`)
  return Boolean(validate(value))
}
