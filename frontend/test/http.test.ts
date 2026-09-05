import assert from 'node:assert/strict'
import test from 'node:test'
import { humanMessage, postFile } from '../src/api/http.ts'

test('humanMessage displays the safe typed payment error message', () => {
  assert.equal(
    humanMessage(422, {
      detail: { code: 'PAYMENT_INVALID_INPUT', message: 'The payment input is invalid.' },
    }),
    'The payment input is invalid.',
  )
})

test('humanMessage preserves legacy FastAPI, reason, and string details', () => {
  assert.equal(humanMessage(422, { detail: [{ msg: 'Not a UUID' }] }), 'Not a UUID')
  assert.equal(humanMessage(409, { detail: { reason: 'Conflict' } }), 'Conflict')
  assert.equal(humanMessage(400, { detail: 'Legacy detail' }), 'Legacy detail')
})

test('postFile sends one original-named file part with POST and lets fetch set multipart Content-Type', async () => {
  const originalFetch = globalThis.fetch
  let options: RequestInit | undefined
  globalThis.fetch = (async (_url, next) => {
    options = next
    return new Response(JSON.stringify({ ok: true }), { headers: { 'Content-Type': 'application/json' } })
  }) as typeof fetch
  try {
    const file = new File(['%PDF-1.7'], 'original filename.pdf', { type: 'application/pdf' })
    await postFile('/api/v1/invoices/id/artifacts', file)
    assert.equal(options?.method, 'POST')
    assert.ok(options?.body instanceof FormData)
    const parts = [...(options!.body as FormData).entries()]
    assert.equal(parts.length, 1)
    assert.equal(parts[0][0], 'file')
    assert.equal((parts[0][1] as File).name, 'original filename.pdf')
    assert.equal(new Headers(options?.headers).has('Content-Type'), false)
  } finally {
    globalThis.fetch = originalFetch
  }
})
