import assert from 'node:assert/strict'
import test from 'node:test'
import { nextTick, reactive } from 'vue'
import {
  openReceiptDialog,
  persistedReceiptCustomer,
  receiptAuditTarget,
} from '../src/utils/receiptEmail.ts'
import { useDocumentSendContext } from '../src/composables/useDocumentSendContext.ts'

const customers = [
  { id: 'en', email: 'en@example.test', locale: 'en' as const },
  { id: 'zh', email: 'zh@example.test', locale: 'zh' as const },
]

test('receipt customer always follows the persisted document customer', () => {
  assert.deepEqual(persistedReceiptCustomer('zh', customers), customers[1])
  assert.equal(persistedReceiptCustomer('missing', customers), null)
  // An unsaved selector change must not change an existing receipt source.
  assert.equal(persistedReceiptCustomer('en', customers)?.email, 'en@example.test')
})

test('audit routing refreshes invoices and exposes quote-source logs', () => {
  assert.equal(receiptAuditTarget('invoice-1', { related_type: 'INVOICE', related_id: 'invoice-1' }), 'refresh-invoice')
  assert.deepEqual(receiptAuditTarget('invoice-1', { related_type: 'QUOTE', related_id: 'quote-9' }), { quoteId: 'quote-9' })
  assert.equal(receiptAuditTarget('invoice-1', { related_type: 'REFUND', related_id: 'refund-9' }), null)
})

test('receipt dialog blocks every payment switch while a send is active', () => {
  assert.deepEqual(openReceiptDialog('payment-a', null, false), { paymentId: 'payment-a', show: true })
  assert.equal(openReceiptDialog('payment-b', 'payment-a', true), null)
  assert.equal(openReceiptDialog('payment-a', 'payment-a', true), null)
})

test('production DocumentSendDialog context state reloads B and never lets A close it', async () => {
  const props = reactive({
    show: true,
    docType: 'receipt' as const,
    docId: 'payment-a',
    customerEmail: 'a@example.test' as string | null,
    customerLocale: 'en' as 'en' | 'zh' | null,
  })
  const applied: string[] = []
  const pending: Array<{
    context: { docId: string }
    resolve: () => void
    isCurrent: () => boolean
  }> = []
  let resets = 0
  const dialog = useDocumentSendContext(
    () => ({ ...props }),
    () => { resets += 1 },
    async (context, isCurrent) => {
      await new Promise<void>(resolve => pending.push({ context, resolve, isCurrent }))
      if (isCurrent()) applied.push(context.docId)
    },
  )

  await nextTick()
  assert.equal(pending[0].context.docId, 'payment-a')
  props.docId = 'payment-b'
  props.customerEmail = 'b@example.test'
  props.customerLocale = 'zh'
  await nextTick()
  assert.equal(pending[1].context.docId, 'payment-b')
  pending[1].resolve()
  await Promise.resolve()
  pending[0].resolve()
  await Promise.resolve()
  assert.deepEqual(applied, ['payment-b'])

  const frozen = dialog.beginSend()
  assert.ok(frozen)
  assert.equal(dialog.sending.value, true)
  props.docId = 'payment-c'
  await nextTick()
  assert.equal(pending.length, 2, 'a send defers replacement-context loading')
  assert.equal(dialog.finishSend(frozen), false)
  await nextTick()
  assert.equal(pending[2].context.docId, 'payment-c')
  pending[2].resolve()
  await Promise.resolve()
  assert.deepEqual(applied, ['payment-b', 'payment-c'])
  assert.ok(resets >= 3)
})
