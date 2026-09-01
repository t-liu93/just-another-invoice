import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import en from '../../src/locales/en.json'
import { localDateStr } from '../../src/utils/date'

const api = vi.hoisted(() => {
  class StableApiError extends Error {
    detail: unknown

    constructor(code: string) {
      super(`stable ${code}`)
      this.name = 'ApiError'
      this.detail = { detail: { code } }
    }
  }
  return {
    http: { get: vi.fn(), post: vi.fn(), put: vi.fn(), del: vi.fn() },
    StableApiError,
  }
})
vi.mock('../../src/api/http', () => ({ ...api.http, downloadBlob: vi.fn(), ApiError: api.StableApiError }))
const router = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn() }))
vi.mock('vue-router', () => ({ useRouter: () => router }))

import DocumentWorkflowPanel from '../../src/components/DocumentWorkflowPanel.vue'

const i18n = createI18n({ legacy: false, locale: 'en', messages: { en } })
const today = (): string => localDateStr(new Date())
const stableErrorCode = 'FORMAL_CHAIN_REQUIRED'
const stableErrorText = (en as { workflow: { errors: Record<string, string> } }).workflow.errors[stableErrorCode]
const chain = {
  settlement_mode: 'FORMAL_ADVANCE', totals: {}, nodes: [], relations: [], events: [], timeline: [],
  available_actions: [{ code: 'CREATE_ADVANCE', available: true, target_id: 'quote', target_type: 'QUOTE' }],
}
const credit = {
  id: 'credit', document_kind: 'CREDIT_NOTE', status: 'SENT', quote_id: 'quote',
  invoice_date: '2026-03-05', currency: 'EUR', total_incl_vat: '10',
}
const cancellationPreview = {
  preview_token: 'frozen-preview-token',
  sources: [{
    source_invoice_id: 'source', source_invoice_number: 'INV-1', document_kind: 'ADVANCE',
    remaining_net_amount: '10', remaining_vat_amount: '2.1', remaining_gross_amount: '12.1',
  }],
}

type Workflow = 'Final' | 'Refund' | 'Replacement' | 'Compensation' | 'Cancellation'
type Outcome = 'success' | 'failure'
const matrix: Array<{ workflow: Workflow; width: number; outcome: Outcome }> = (
  ['Final', 'Refund', 'Replacement', 'Compensation', 'Cancellation'] as Workflow[]
).flatMap(workflow => [320, 375].flatMap(width => (
  ['success', 'failure'] as Outcome[]
).map(outcome => ({ workflow, width, outcome }))))

function buttonByText(root: ParentNode, label: string): HTMLButtonElement {
  const button = [...root.querySelectorAll('button')].find(item => item.textContent?.includes(label))
  if (!button) throw new Error(`Missing button: ${label}`)
  return button as HTMLButtonElement
}

function dialog(): HTMLElement {
  const modal = [...document.body.querySelectorAll('.workflow-modal')].at(-1)
  if (!modal) throw new Error('Missing teleported workflow modal')
  return modal as HTMLElement
}

function postCalls(url: string): unknown[][] {
  return api.http.post.mock.calls.filter(([calledUrl]) => calledUrl === url)
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (cause: unknown) => void
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail })
  return { promise, resolve, reject }
}

function setViewport(width: number): void {
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: width })
  window.dispatchEvent(new Event('resize'))
}

function fillActualInput(root: ParentNode, value: string): void {
  const input = root.querySelector('input') as HTMLInputElement | null
  if (!input) throw new Error('Missing visible input')
  input.value = value
  input.dispatchEvent(new Event('input', { bubbles: true }))
}

function assertOpenFailure(modal: HTMLElement, positiveLabel: string): void {
  expect(modal.isConnected).toBe(true)
  // This class is the mobile viewport-bounded scrolling owner, rather than a
  // test-only wrapper around Naive's teleported content.
  expect(modal.classList.contains('workflow-modal')).toBe(true)
  expect(modal.textContent).toContain(stableErrorText)
  const positive = buttonByText(modal, positiveLabel)
  expect(positive.isConnected).toBe(true)
  expect(positive.disabled).toBe(false)
}

function assertClosed(): void {
  // Naive keeps a transition shell mounted in happy-dom. Product close state
  // is therefore its teleported modal being hidden, rather than DOM removal.
  expect([...document.body.querySelectorAll<HTMLElement>('.workflow-modal')].every(modal => modal.style.display === 'none')).toBe(true)
}

describe('DocumentWorkflowPanel actual Naive viewport command matrix', () => {
  beforeEach(() => {
    class ResizeObserver { observe(): void {} unobserve(): void {} disconnect(): void {} }
    vi.stubGlobal('ResizeObserver', ResizeObserver)
    vi.stubGlobal('matchMedia', (query: string) => ({
      matches: query.includes('max-width') && window.innerWidth <= 640, media: query,
      addEventListener: vi.fn(), removeEventListener: vi.fn(), addListener: vi.fn(), removeListener: vi.fn(), dispatchEvent: vi.fn(),
    }))
    api.http.get.mockReset()
    api.http.get.mockResolvedValue({ items: [] })
    api.http.post.mockReset()
    api.http.put.mockReset()
    api.http.del.mockReset()
    router.push.mockReset()
    router.replace.mockReset()
  })

  afterEach(() => { document.body.innerHTML = ''; vi.unstubAllGlobals() })

  it('keeps workflow totals and actions visible while the actual Document Chain feed starts collapsed', async () => {
    setViewport(320)
    const host = document.createElement('div')
    document.body.appendChild(host)
    mount(DocumentWorkflowPanel, {
      attachTo: host,
      props: {
        quoteId: 'quote',
        documentChain: {
          ...chain,
          totals: { due_amount: '90.00' },
          timeline: [{
            kind: 'EVENT', order: 1,
            event: { id: 'event', event_type: 'MODE_LOCKED', occurred_at: '2026-08-31T10:00:00Z', event_order: 1 },
          }],
        },
      },
      global: { plugins: [i18n] },
    })
    await flushPromises()

    expect(host.textContent).toContain('90.00')
    expect(host.textContent).toContain('Create advance')
    const header = [...host.querySelectorAll<HTMLElement>('.n-collapse-item__header')]
      .find(item => item.textContent?.includes('Document chain'))
    expect(header).toBeDefined()
    expect(host.querySelector('.n-collapse-item__content-wrapper')).toBeNull()

    header?.querySelector<HTMLElement>('.n-collapse-item__header-main')?.click()
    await flushPromises()
    expect(host.querySelector('.n-collapse-item__content-wrapper')).not.toBeNull()
    expect(host.textContent).toContain('Billing mode locked')

    header?.querySelector<HTMLElement>('.n-collapse-item__header-main')?.click()
    await flushPromises()
    expect(host.querySelector('.n-collapse-item__content-wrapper')).toBeNull()
    expect(host.textContent).toContain('90.00')
    expect(host.textContent).toContain('Create advance')
  })

  it('loads issued output on a same-id DRAFT to SENT transition, then clears it without a GET on SENT to DRAFT', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)
    api.http.get.mockResolvedValue({ items: [{ id: 'artifact', filename: 'issued.pdf', locale: 'en', creation_reason: 'DOWNLOAD' }] })
    const wrapper = mount(DocumentWorkflowPanel, {
      attachTo: host,
      props: { invoice: { ...credit, document_kind: 'STANDARD', status: 'DRAFT' }, documentChain: chain },
      global: { plugins: [i18n] },
    })
    await flushPromises()
    expect(api.http.get).not.toHaveBeenCalled()

    await wrapper.setProps({ invoice: { ...credit, document_kind: 'STANDARD', status: 'SENT' } })
    await flushPromises()
    expect(api.http.get).toHaveBeenCalledWith('/api/v1/invoices/credit/artifacts')
    expect(host.textContent).toContain('issued.pdf')

    const callsBeforeDraft = api.http.get.mock.calls.length
    await wrapper.setProps({ invoice: { ...credit, document_kind: 'STANDARD', status: 'DRAFT' } })
    await flushPromises()
    expect(api.http.get).toHaveBeenCalledTimes(callsBeforeDraft)
    expect(host.textContent).not.toContain('issued.pdf')
  })

  it.each(['STANDARD', 'CREDIT_NOTE'] as const)('keeps only the new issued $kind owner output when invoice A switches to invoice B', async kind => {
    const host = document.createElement('div')
    document.body.appendChild(host)
    const aInvoiceArtifacts = deferred<{ items: unknown[] }>()
    const bInvoiceArtifacts = deferred<{ items: unknown[] }>()
    const aRefunds = deferred<{ items: unknown[] }>()
    const bRefunds = deferred<{ items: unknown[] }>()
    const bRefundArtifacts = deferred<{ items: unknown[] }>()
    api.http.get.mockImplementation((url: string) => {
      if (url === '/api/v1/invoices/invoice-a/artifacts') return aInvoiceArtifacts.promise
      if (url === '/api/v1/invoices/invoice-b/artifacts') return bInvoiceArtifacts.promise
      if (url === '/api/v1/credit-notes/invoice-a/refunds') return aRefunds.promise
      if (url === '/api/v1/credit-notes/invoice-b/refunds') return bRefunds.promise
      if (url === '/api/v1/payments/refund-b/artifacts') return bRefundArtifacts.promise
      throw new Error(`unexpected issued-output request: ${url}`)
    })
    const invoice = (id: string) => ({
      ...credit, id, document_kind: kind, status: 'SENT', quote_id: `quote-${id}`,
    })
    const wrapper = mount(DocumentWorkflowPanel, {
      attachTo: host,
      props: { invoice: invoice('invoice-a'), documentChain: chain },
      global: { plugins: [i18n] },
    })
    await flushPromises()

    await wrapper.setProps({ invoice: invoice('invoice-b') })
    await flushPromises()

    // Each owner begins exactly one issued-output request. A credit note has
    // its invoice artifact and refund collection in parallel.
    const calls = api.http.get.mock.calls.map(([url]) => url)
    expect(calls.filter(url => url === '/api/v1/invoices/invoice-a/artifacts')).toHaveLength(1)
    expect(calls.filter(url => url === '/api/v1/invoices/invoice-b/artifacts')).toHaveLength(1)
    if (kind === 'CREDIT_NOTE') {
      expect(calls.filter(url => url === '/api/v1/credit-notes/invoice-a/refunds')).toHaveLength(1)
      expect(calls.filter(url => url === '/api/v1/credit-notes/invoice-b/refunds')).toHaveLength(1)
      bRefunds.resolve({ items: [{ id: 'refund-b', payment_date: '2026-03-06', amount: '3.00', reference: 'B' }] })
    }
    bInvoiceArtifacts.resolve({ items: [{ id: 'artifact-b', filename: 'invoice-b.pdf', locale: 'en', creation_reason: 'DOWNLOAD' }] })
    await flushPromises()
    if (kind === 'CREDIT_NOTE') {
      expect(api.http.get).toHaveBeenCalledWith('/api/v1/payments/refund-b/artifacts')
      bRefundArtifacts.resolve({ items: [{ id: 'refund-artifact-b', filename: 'refund-b.pdf', locale: 'en', creation_reason: 'DOWNLOAD' }] })
      await flushPromises()
    }

    // A settles only after B is visible. Its stale generation may not replace
    // B or start a payment-artifact request for A's refund.
    aInvoiceArtifacts.resolve({ items: [{ id: 'artifact-a', filename: 'invoice-a.pdf', locale: 'en', creation_reason: 'DOWNLOAD' }] })
    if (kind === 'CREDIT_NOTE') aRefunds.resolve({ items: [{ id: 'refund-a', payment_date: '2026-03-05', amount: '2.00', reference: 'A' }] })
    await flushPromises()

    expect(host.textContent).toContain('invoice-b.pdf')
    expect(host.textContent).not.toContain('invoice-a.pdf')
    expect(host.textContent).not.toContain('refund-a')
    if (kind === 'CREDIT_NOTE') {
      expect(host.textContent).toContain('refund-b.pdf')
      expect(api.http.get).not.toHaveBeenCalledWith('/api/v1/payments/refund-a/artifacts')
    }
  })

  it('clears an issued-output error when the same invoice returns to DRAFT', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)
    api.http.get.mockRejectedValueOnce(new api.StableApiError(stableErrorCode))
    const wrapper = mount(DocumentWorkflowPanel, {
      attachTo: host,
      props: { invoice: { ...credit, document_kind: 'STANDARD', status: 'SENT' }, documentChain: chain },
      global: { plugins: [i18n] },
    })
    await flushPromises()
    expect((wrapper.vm as any).resourceError).toBe(stableErrorText)

    await wrapper.setProps({ invoice: { ...credit, document_kind: 'STANDARD', status: 'DRAFT' } })
    await flushPromises()

    expect((wrapper.vm as any).resourceError).toBeNull()
    expect((wrapper.vm as any).issuedOutputLoading).toBe(false)
    expect(host.textContent).not.toContain(stableErrorText)
  })

  it.each(['success', 'failure'] as const)('does not clear a workflow mutation error when same-id DRAFT becomes SENT and output load %s', async outcome => {
    const host = document.createElement('div')
    document.body.appendChild(host)
    if (outcome === 'failure') api.http.get.mockRejectedValueOnce(new api.StableApiError(stableErrorCode))
    else api.http.get.mockResolvedValueOnce({ items: [] })
    const wrapper = mount(DocumentWorkflowPanel, {
      attachTo: host,
      props: { invoice: { ...credit, document_kind: 'STANDARD', status: 'DRAFT' }, documentChain: chain },
      global: { plugins: [i18n] },
    })
    await flushPromises()
    ;(wrapper.vm as any).error = stableErrorText

    await wrapper.setProps({ invoice: { ...credit, document_kind: 'STANDARD', status: 'SENT' } })
    await flushPromises()

    expect((wrapper.vm as any).error).toBe(stableErrorText)
    expect(host.textContent).toContain(stableErrorText)
  })

  it('keeps an issued-output failure visible through a successful chain refresh and clears it after output retry', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)
    const refreshChain = vi.fn().mockResolvedValue(true)
    api.http.get.mockRejectedValueOnce(new api.StableApiError(stableErrorCode))
    const wrapper = mount(DocumentWorkflowPanel, {
      attachTo: host,
      props: { invoice: { ...credit, document_kind: 'STANDARD' }, documentChain: chain, refreshChain },
      global: { plugins: [i18n] },
    })
    await flushPromises()
    expect(host.textContent).toContain(stableErrorText)

    // A successful parent-chain request must not hide another failed retry of
    // the independently owned output resource.
    api.http.get.mockRejectedValueOnce(new api.StableApiError(stableErrorCode))
    buttonByText(host, 'Refresh').click()
    await flushPromises()
    expect(refreshChain).toHaveBeenCalledTimes(1)
    expect(api.http.get).toHaveBeenCalledTimes(2)
    expect(host.textContent).toContain(stableErrorText)

    api.http.get.mockResolvedValueOnce({ items: [] })
    buttonByText(host, 'Refresh').click()
    await flushPromises()
    expect(refreshChain).toHaveBeenCalledTimes(2)
    expect(api.http.get).toHaveBeenCalledTimes(3)
    expect(host.textContent).not.toContain(stableErrorText)
    expect((wrapper.vm as any).error).toBeNull()
  })

  it('keeps collapse expanded on same-owner refresh but resets it for an invoice-to-quote same-id owner change', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)
    const refreshChain = vi.fn().mockResolvedValue(true)
    const wrapper = mount(DocumentWorkflowPanel, {
      attachTo: host,
      props: { invoice: { ...credit, document_kind: 'STANDARD', status: 'DRAFT', id: 'same-id' }, documentChain: chain, refreshChain },
      global: { plugins: [i18n] },
    })
    await flushPromises()
    const header = () => [...host.querySelectorAll<HTMLElement>('.n-collapse-item__header')]
      .find(item => item.textContent?.includes('Document chain'))
    header()?.querySelector<HTMLElement>('.n-collapse-item__header-main')?.click()
    await flushPromises()
    expect(host.querySelector('.n-collapse-item__content-wrapper')).not.toBeNull()

    buttonByText(host, 'Refresh').click()
    await flushPromises()
    expect(refreshChain).toHaveBeenCalledTimes(1)
    expect(host.querySelector('.n-collapse-item__content-wrapper')).not.toBeNull()

    await wrapper.setProps({ invoice: null, quoteId: 'same-id' })
    await flushPromises()
    expect(host.querySelector('.n-collapse-item__content-wrapper')).toBeNull()
  })

  it('resets every workflow editor when mounted quote A switches to quote B', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)
    const wrapper = mount(DocumentWorkflowPanel, {
      attachTo: host,
      props: { quoteId: 'quote-a', documentChain: chain },
      global: { plugins: [i18n] },
    })
    await flushPromises()
    Object.assign(wrapper.vm as any, {
      advanceMode: 'PERCENTAGE', advanceRaw: '25', advanceDate: '2026-03-04', advanceReference: 'advance-a', showAdvance: true,
      creditFull: false, creditIntentConfirmation: true, creditRows: [{ source_basis_line_id: 'line-a', input_mode: 'QUANTITY', raw: '2' }], creditDate: '2026-03-05', creditReference: 'credit-a', showCredit: true,
      finalDate: '2026-03-06', showFinal: true, showCancellation: true, refundAmount: '12', refundReference: 'refund-a', refundSendId: 'refund-a', refundSendShow: true,
      error: stableErrorText, busy: true,
    })

    await wrapper.setProps({ quoteId: 'quote-b' })
    await flushPromises()

    const vm = wrapper.vm as any
    expect(vm.advanceMode).toBe('GROSS_AMOUNT')
    expect(vm.advanceRaw).toBe('')
    expect(vm.advanceReference).toBeNull()
    expect(vm.creditFull).toBe(true)
    expect(vm.creditIntentConfirmation).toBe(false)
    expect(vm.creditRows).toEqual([])
    expect(vm.creditReference).toBeNull()
    expect(vm.finalDate).toBe(today())
    expect(vm.showAdvance || vm.showFinal || vm.showCredit || vm.showCancellation).toBe(false)
    expect(vm.refundAmount).toBe('')
    expect(vm.refundReference).toBeNull()
    expect(vm.refundSendId).toBeNull()
    expect(vm.refundSendShow).toBe(false)
    expect(vm.error).toBeNull()
    expect(vm.busy).toBe(false)
  })

  it.each(['success', 'reject'] as const)('does not let a deferred quote A refresh %s write quote B state', async outcome => {
    const host = document.createElement('div')
    document.body.appendChild(host)
    const pending = deferred<boolean>()
    const refreshChain = vi.fn(() => pending.promise)
    const wrapper = mount(DocumentWorkflowPanel, {
      attachTo: host,
      props: { quoteId: 'quote-a', documentChain: chain, refreshChain },
      global: { plugins: [i18n] },
    })
    await flushPromises()
    buttonByText(host, 'Refresh').click()
    await flushPromises()
    expect(refreshChain).toHaveBeenCalledTimes(1)

    await wrapper.setProps({ quoteId: 'quote-b' })
    await flushPromises()
    if (outcome === 'success') pending.resolve(false)
    else pending.reject(new api.StableApiError(stableErrorCode))
    await flushPromises()

    expect((wrapper.vm as any).error).toBeNull()
    expect((wrapper.vm as any).resourceError).toBeNull()
  })

  it.each(matrix)('$workflow at $width px keeps the real modal semantics on $outcome', async ({ workflow, width, outcome }) => {
    setViewport(width)
    const host = document.createElement('div')
    document.body.appendChild(host)
    const succeeds = outcome === 'success'

    if (workflow === 'Final') {
      api.http.post.mockImplementationOnce(() => succeeds ? Promise.resolve({ id: 'final-draft' }) : Promise.reject(new api.StableApiError(stableErrorCode)))
      mount(DocumentWorkflowPanel, {
        attachTo: host,
        props: { quoteId: 'quote', documentChain: { ...chain, available_actions: [{ code: 'CREATE_FINAL', available: true, target_id: 'quote', target_type: 'QUOTE' }] } },
        global: { plugins: [i18n] },
      })
      await flushPromises()
      buttonByText(host, 'Create final').click()
      await flushPromises()
      const modal = dialog()
      buttonByText(modal, 'Create draft').click()
      await flushPromises()
      const endpoint = '/api/v1/quotes/quote/final-invoice'
      expect(postCalls(endpoint)).toEqual([[endpoint, { invoice_date: today() }]])
      if (succeeds) {
        assertClosed()
        expect(router.push).toHaveBeenCalledWith('/invoices/final-draft/edit')
      } else assertOpenFailure(modal, 'Create draft')
      return
    }

    if (workflow === 'Refund') {
      api.http.post.mockImplementationOnce(() => succeeds ? Promise.resolve({ credit_note_id: 'credit', items: [] }) : Promise.reject(new api.StableApiError(stableErrorCode)))
      mount(DocumentWorkflowPanel, { attachTo: host, props: { invoice: credit, documentChain: chain }, global: { plugins: [i18n] } })
      await flushPromises()
      fillActualInput(host, '12.34')
      await flushPromises()
      buttonByText(host, 'Record refund').click()
      await flushPromises()
      const modal = dialog()
      buttonByText(modal, 'Confirm').click()
      await flushPromises()
      const endpoint = '/api/v1/credit-notes/credit/refunds'
      expect(postCalls(endpoint)).toEqual([[endpoint, {
        payment_date: today(), amount: '12.34', payment_method_id: null, reference: null, note: null,
      }]])
      if (succeeds) assertClosed()
      else assertOpenFailure(modal, 'Confirm')
      return
    }

    if (workflow === 'Replacement' || workflow === 'Compensation') {
      const path = workflow === 'Replacement' ? 'replacement' : 'compensating-invoice'
      const code = workflow === 'Replacement' ? 'CREATE_REPLACEMENT' : 'CREATE_COMPENSATING_INVOICE'
      const label = workflow === 'Replacement' ? 'Create replacement draft' : 'Create compensating draft'
      const relation = workflow === 'Replacement' ? 'REPLACEMENT_OF' : 'COMPENSATES_CREDIT'
      api.http.post.mockImplementationOnce(() => succeeds ? Promise.resolve({ id: `${path}-draft` }) : Promise.reject(new api.StableApiError(stableErrorCode)))
      mount(DocumentWorkflowPanel, {
        attachTo: host,
        props: { invoice: credit, documentChain: { ...chain, available_actions: [{
          code, available: true, target_id: 'credit', target_type: 'INVOICE',
          followup_context: { credit_note_id: 'credit', source_invoice_id: 'source', relation_type: relation, target_document_kind: 'STANDARD', gross_amount: '10.00' },
        }] } },
        global: { plugins: [i18n] },
      })
      await flushPromises()
      buttonByText(host, label).click()
      await flushPromises()
      const modal = dialog()
      // The confirmation is populated from the authoritative follow-up context,
      // not a caller-provided request body.
      expect(modal.textContent).toContain('source')
      expect(modal.textContent).toContain('10.00')
      buttonByText(modal, 'Confirm').click()
      await flushPromises()
      const endpoint = `/api/v1/credit-notes/credit/${path}`
      expect(postCalls(endpoint)).toEqual([[endpoint, {}]])
      if (succeeds) {
        assertClosed()
        expect(router.push).toHaveBeenCalledWith(`/invoices/${path}-draft/edit`)
      } else assertOpenFailure(modal, 'Confirm')
      return
    }

    // Cancellation has an actual card flow before its real Naive positive
    // dialog. The preview token and date are frozen into the confirmation body.
    api.http.post.mockResolvedValueOnce(cancellationPreview)
    api.http.post.mockImplementationOnce(() => succeeds ? Promise.resolve({ created_credit_note_ids: ['draft'] }) : Promise.reject(new api.StableApiError(stableErrorCode)))
    mount(DocumentWorkflowPanel, {
      attachTo: host,
      props: { quoteId: 'quote', documentChain: { ...chain, available_actions: [{ code: 'CREATE_PROJECT_CANCELLATION', available: true, target_id: 'quote', target_type: 'QUOTE' }] } },
      global: { plugins: [i18n] },
    })
    await flushPromises()
    buttonByText(host, 'Cancel formal project').click()
    await flushPromises()
    const card = dialog()
    buttonByText(card, 'Preview cancellation').click()
    await flushPromises()
    buttonByText(card, 'Create all credit drafts').click()
    await flushPromises()
    const modal = dialog()
    expect(modal.textContent).toContain('INV-1')
    expect(modal.textContent).toContain('12.1')
    buttonByText(modal, 'Confirm').click()
    await flushPromises()
    const previewEndpoint = '/api/v1/quotes/quote/cancellation/preview'
    const endpoint = '/api/v1/quotes/quote/cancellation/create-credit-drafts'
    expect(postCalls(previewEndpoint)).toEqual([[previewEndpoint, { invoice_date: today() }]])
    expect(postCalls(endpoint)).toEqual([[endpoint, { invoice_date: today(), preview_token: 'frozen-preview-token' }]])
    if (succeeds) assertClosed()
    else assertOpenFailure(modal, 'Confirm')
  })
})
