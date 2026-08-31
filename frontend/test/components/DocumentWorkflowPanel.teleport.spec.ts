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
