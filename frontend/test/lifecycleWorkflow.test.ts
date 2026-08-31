import assert from 'node:assert/strict'
import test from 'node:test'
import { advanceIntent, availableAction, creditIntent, uniqueTimelineNodes } from '../src/utils/lifecycleWorkflow.ts'

test('available action mapping only trusts a positive backend projection', () => {
  assert.equal(availableAction([{ code: 'CREATE_FINAL', available: true }], 'CREATE_FINAL'), true)
  assert.equal(availableAction([{ code: 'CREATE_FINAL', available: false }], 'CREATE_FINAL'), false)
  assert.equal(availableAction(undefined, 'CREATE_FINAL'), false)
})

test('advance builder preserves raw gross or percentage intent without money math', () => {
  assert.deepEqual(advanceIntent('GROSS_AMOUNT', '123.456'), { input_mode: 'GROSS_AMOUNT', gross_amount: '123.456' })
  assert.deepEqual(advanceIntent('PERCENTAGE', '50.125'), { input_mode: 'PERCENTAGE', percentage: '50.125' })
})

test('credit builder sends only source basis and one raw quantity or gross input', () => {
  assert.deepEqual(creditIntent(true, []), { full_remaining: true })
  assert.deepEqual(creditIntent(false, [
    { source_basis_line_id: 'a', input_mode: 'QUANTITY', raw: '2.5' },
    { source_basis_line_id: 'b', input_mode: 'GROSS_AMOUNT', raw: '10.01' },
    { source_basis_line_id: '', input_mode: 'QUANTITY', raw: '9' },
  ]), {
    full_remaining: false,
    lines: [
      { source_basis_line_id: 'a', input_mode: 'QUANTITY', quantity: '2.5' },
      { source_basis_line_id: 'b', input_mode: 'GROSS_AMOUNT', gross_amount: '10.01' },
    ],
  })
})

test('timeline removes duplicate typed nodes without collapsing a payment and document namespace', () => {
  const nodes = [
    { id: 'one', node_type: 'INVOICE' },
    { id: 'one', node_type: 'INVOICE' },
    { id: 'one', node_type: 'PAYMENT' },
    { id: 'two', node_type: 'QUOTE' },
  ] as never
  assert.deepEqual(uniqueTimelineNodes(nodes).map(node => `${node.node_type}:${node.id}`), ['INVOICE:one', 'PAYMENT:one', 'QUOTE:two'])
})
