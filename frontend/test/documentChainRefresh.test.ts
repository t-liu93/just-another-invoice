import assert from 'node:assert/strict'
import test from 'node:test'
import { effectScope } from 'vue'
import {
  createDocumentChainPaymentChangeHandler,
  useDocumentChainRefresh,
} from '../src/composables/useDocumentChainRefresh.ts'

interface Chain {
  settlement_mode: string
  events: string[]
}

interface InvoiceAggregate {
  paid_status: string
  status: string
  due_amount: string
}

interface Deferred<T> {
  promise: Promise<T>
  resolve(value: T): void
  reject(error: unknown): void
}

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

test('Quote paymentsChanged refreshes the authoritative RECEIPT_ONLY chain and events', async () => {
  const responses: Chain[] = [
    { settlement_mode: 'UNSET', events: [] },
    { settlement_mode: 'RECEIPT_ONLY', events: ['MODE_LOCKED', 'QUOTE_PAYMENT_CREATED'] },
  ]
  const chain = useDocumentChainRefresh(async () => responses.shift()!)
  const paymentsChanged = createDocumentChainPaymentChangeHandler(chain.refreshAfterPayment)

  await chain.loadInitialDocumentChain()
  assert.equal(await paymentsChanged(undefined), true)

  assert.equal(chain.documentChain.value?.settlement_mode, 'RECEIPT_ONLY')
  assert.deepEqual(chain.documentChain.value?.events, ['MODE_LOCKED', 'QUOTE_PAYMENT_CREATED'])
  assert.equal(chain.paymentRefreshError.value, null)
})

test('Invoice create/update/delete paymentsChanged applies the aggregate before each chain refresh', async () => {
  const events = [
    'INVOICE_PAYMENT_CREATED',
    'INVOICE_PAYMENT_UPDATED',
    'INVOICE_PAYMENT_DELETED',
  ]
  const chain = useDocumentChainRefresh(async () => ({
    settlement_mode: 'DIRECT_INVOICE',
    events: [events.shift()!],
  }))
  let invoice: InvoiceAggregate = { paid_status: 'UNPAID', status: 'SENT', due_amount: '100.00' }
  const paymentsChanged = createDocumentChainPaymentChangeHandler(
    chain.refreshAfterPayment,
    (aggregate: InvoiceAggregate) => { invoice = aggregate },
  )

  for (const [event, aggregate] of [
    ['INVOICE_PAYMENT_CREATED', { paid_status: 'PARTIALLY_PAID', status: 'SENT', due_amount: '60.00' }],
    ['INVOICE_PAYMENT_UPDATED', { paid_status: 'PARTIALLY_PAID', status: 'SENT', due_amount: '50.00' }],
    ['INVOICE_PAYMENT_DELETED', { paid_status: 'UNPAID', status: 'SENT', due_amount: '100.00' }],
  ] as const) {
    assert.equal(await paymentsChanged(aggregate), true)
    assert.deepEqual(invoice, aggregate)
    assert.deepEqual(chain.documentChain.value?.events, [event])
  }
})

test('initial and payment-refresh failures have separate retries and preserve successful payment state', async () => {
  const initialFailure = new Error('initial read failed')
  const paymentRefreshFailure = new Error('payment refresh failed')
  let phase: 'initial-fail' | 'initial-success' | 'payment-fail' | 'payment-success' = 'initial-fail'
  const chain = useDocumentChainRefresh(async () => {
    if (phase === 'initial-fail') throw initialFailure
    if (phase === 'payment-fail') throw paymentRefreshFailure
    return phase === 'initial-success'
      ? { settlement_mode: 'DIRECT_INVOICE', events: ['INVOICE_PAYMENT_CREATED'] }
      : { settlement_mode: 'DIRECT_INVOICE', events: ['INVOICE_PAYMENT_UPDATED'] }
  })
  let invoice: InvoiceAggregate = { paid_status: 'UNPAID', status: 'SENT', due_amount: '100.00' }
  const paymentsChanged = createDocumentChainPaymentChangeHandler(
    chain.refreshAfterPayment,
    (aggregate: InvoiceAggregate) => { invoice = aggregate },
  )

  assert.equal(await chain.loadInitialDocumentChain(), false)
  assert.equal(chain.initialChainError.value, initialFailure)
  assert.equal(chain.paymentRefreshError.value, null)
  assert.equal(chain.documentChain.value, null)

  phase = 'initial-success'
  assert.equal(await chain.loadInitialDocumentChain(), true)
  assert.equal(chain.initialChainError.value, null)
  assert.deepEqual(chain.documentChain.value?.events, ['INVOICE_PAYMENT_CREATED'])

  phase = 'initial-fail'
  assert.equal(await chain.loadInitialDocumentChain(), false)
  assert.equal(chain.initialChainError.value, initialFailure)
  assert.deepEqual(chain.documentChain.value?.events, ['INVOICE_PAYMENT_CREATED'])

  phase = 'initial-success'
  assert.equal(await chain.loadInitialDocumentChain(), true)
  assert.equal(chain.initialChainError.value, null)

  phase = 'payment-fail'
  const successfulAggregate = { paid_status: 'PARTIALLY_PAID', status: 'SENT', due_amount: '60.00' }
  assert.equal(await paymentsChanged(successfulAggregate), false)
  assert.deepEqual(invoice, successfulAggregate)
  assert.equal(chain.initialChainError.value, null)
  assert.equal(chain.paymentRefreshError.value, paymentRefreshFailure)
  assert.deepEqual(chain.documentChain.value?.events, ['INVOICE_PAYMENT_CREATED'])

  phase = 'payment-success'
  assert.equal(await chain.refreshAfterPayment(), true)
  assert.equal(chain.paymentRefreshError.value, null)
  assert.deepEqual(chain.documentChain.value?.events, ['INVOICE_PAYMENT_UPDATED'])
})

test('latest document-chain request wins when deferred responses resolve out of order', async () => {
  const first = deferred<Chain>()
  const second = deferred<Chain>()
  const responses = [first.promise, second.promise]
  const chain = useDocumentChainRefresh(() => responses.shift()!)

  const firstRefresh = chain.refreshAfterPayment()
  const secondRefresh = chain.refreshAfterPayment()
  first.resolve({ settlement_mode: 'UNSET', events: ['STALE'] })
  assert.equal(await firstRefresh, false)
  assert.equal(chain.documentChain.value, null)
  assert.equal(chain.chainRefreshing.value, true)

  second.resolve({ settlement_mode: 'RECEIPT_ONLY', events: ['MODE_LOCKED'] })
  assert.equal(await secondRefresh, true)
  assert.deepEqual(chain.documentChain.value, {
    settlement_mode: 'RECEIPT_ONLY',
    events: ['MODE_LOCKED'],
  })
  assert.equal(chain.chainRefreshing.value, false)
})

test('route reset clears its own chain and invalidates both late success and failure', async () => {
  for (const outcome of ['success', 'failure'] as const) {
    const request = deferred<Chain>()
    const chain = useDocumentChainRefresh(() => request.promise)
    chain.documentChain.value = { settlement_mode: 'A', events: ['OLD'] }
    const pending = chain.loadInitialDocumentChain()
    chain.resetDocumentChain()
    assert.equal(chain.documentChain.value, null)
    assert.equal(chain.chainRefreshing.value, false)
    if (outcome === 'success') request.resolve({ settlement_mode: 'B', events: ['NEW'] })
    else request.reject(new Error('late B failure'))
    assert.equal(await pending, false)
    assert.equal(chain.documentChain.value, null)
    assert.equal(chain.initialChainError.value, null)
    assert.equal(chain.chainRefreshing.value, false)
  }
})

test('scope disposal invalidates deferred success and failure without further state writes', async () => {
  for (const outcome of ['success', 'failure'] as const) {
    const request = deferred<Chain>()
    const scope = effectScope()
    let chain!: ReturnType<typeof useDocumentChainRefresh<Chain>>
    scope.run(() => {
      chain = useDocumentChainRefresh(() => request.promise)
    })
    const pending = chain.loadInitialDocumentChain()
    const beforeDispose = {
      documentChain: chain.documentChain.value,
      initialError: chain.initialChainError.value,
      paymentError: chain.paymentRefreshError.value,
      refreshing: chain.chainRefreshing.value,
    }

    scope.stop()
    if (outcome === 'success') request.resolve({ settlement_mode: 'RECEIPT_ONLY', events: ['MODE_LOCKED'] })
    else request.reject(new Error('late failure'))

    assert.equal(await pending, false)
    assert.deepEqual({
      documentChain: chain.documentChain.value,
      initialError: chain.initialChainError.value,
      paymentError: chain.paymentRefreshError.value,
      refreshing: chain.chainRefreshing.value,
    }, beforeDispose)
    assert.equal(await chain.refreshAfterPayment(), false)
  }
})
