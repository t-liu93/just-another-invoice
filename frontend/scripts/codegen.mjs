#!/usr/bin/env node

import { spawnSync } from 'node:child_process'
import { existsSync, readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const EXPECTED_OPENAPI_TITLE = 'Just Another Invoice'

const scriptDir = dirname(fileURLToPath(import.meta.url))
const frontendRoot = resolve(scriptDir, '..')
const repoRoot = resolve(frontendRoot, '..')

function parseEnvFile(filePath) {
  if (!existsSync(filePath)) return {}

  const env = {}
  const lines = readFileSync(filePath, 'utf8').split(/\r?\n/)

  for (const line of lines) {
    const trimmed = line.trim()
    if (!trimmed || trimmed.startsWith('#')) continue

    const match = trimmed.match(/^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$/)
    if (!match) continue

    const [, key, rawValue] = match
    let value = rawValue.trim()
    if (
      (value.startsWith('"') && value.endsWith('"')) ||
      (value.startsWith("'") && value.endsWith("'"))
    ) {
      value = value.slice(1, -1)
    }
    env[key] = value
  }

  return env
}

function buildSchemaUrl() {
  if (process.env.OPENAPI_URL) return process.env.OPENAPI_URL

  const rootEnv = parseEnvFile(resolve(repoRoot, '.env'))
  const appPort = process.env.APP_PORT || rootEnv.APP_PORT || '8000'
  return `http://localhost:${appPort}/api/v1/openapi.json`
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
