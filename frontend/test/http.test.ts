import assert from 'node:assert/strict'
import test from 'node:test'
import { humanMessage } from '../src/api/http.ts'

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
