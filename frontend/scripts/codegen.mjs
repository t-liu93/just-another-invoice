#!/usr/bin/env node

import { spawnSync } from 'node:child_process'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const EXPECTED_OPENAPI_TITLE = 'Yet Another Ledger'
const DEFAULT_SCHEMA_URL = 'http://localhost:8000/api/v1/openapi.json'

const scriptDir = dirname(fileURLToPath(import.meta.url))
const frontendRoot = resolve(scriptDir, '..')

function buildSchemaUrl() {
  return process.env.OPENAPI_URL || DEFAULT_SCHEMA_URL
}

async function assertExpectedOpenApi(schemaUrl) {
  const response = await fetch(schemaUrl)
  if (!response.ok) {
    throw new Error(`OpenAPI request failed: HTTP ${response.status} ${response.statusText}`)
  }

  const schema = await response.json()
  const title = schema?.info?.title
  if (title !== EXPECTED_OPENAPI_TITLE) {
    throw new Error(
      `Unexpected OpenAPI title ${JSON.stringify(title)} from ${schemaUrl}; ` +
        `expected ${JSON.stringify(EXPECTED_OPENAPI_TITLE)}.`,
    )
  }
}

async function main() {
  const schemaUrl = buildSchemaUrl()
  await assertExpectedOpenApi(schemaUrl)

  const result = spawnSync(
    'npx',
    [
      'openapi-typescript',
      schemaUrl,
      '-o',
      'src/api/schema.d.ts',
      '--default-non-nullable',
      'false',
    ],
    {
      cwd: frontendRoot,
      shell: process.platform === 'win32',
      stdio: 'inherit',
    },
  )

  process.exit(result.status ?? 1)
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : error)
  process.exit(1)
})
