import assert from 'node:assert/strict'
import test from 'node:test'
import { invoiceDocumentKindLabelKey } from '../src/utils/documentKind.ts'

test('invoice document kind labels default compatible responses to Standard', () => {
  assert.equal(invoiceDocumentKindLabelKey(undefined), 'invoices.documentKindSTANDARD')
  assert.equal(invoiceDocumentKindLabelKey(null), 'invoices.documentKindSTANDARD')
})

test('invoice document kind labels use the generated API enum values', () => {
  assert.equal(invoiceDocumentKindLabelKey('STANDARD'), 'invoices.documentKindSTANDARD')
  assert.equal(invoiceDocumentKindLabelKey('ADVANCE'), 'invoices.documentKindADVANCE')
  assert.equal(invoiceDocumentKindLabelKey('FINAL'), 'invoices.documentKindFINAL')
  assert.equal(invoiceDocumentKindLabelKey('CREDIT_NOTE'), 'invoices.documentKindCREDIT_NOTE')
})
