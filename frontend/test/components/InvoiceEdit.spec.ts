import { describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { createMemoryHistory, createRouter } from 'vue-router'
import en from '../../src/locales/en.json'

const http = vi.hoisted(() => ({ get: vi.fn(), downloadBlob: vi.fn() }))
const store = vi.hoisted(() => ({
  fetchInvoice: vi.fn(), fetchProductOptions: vi.fn(), calculatePreview: vi.fn(),
  createInvoice: vi.fn(), updateInvoice: vi.fn(), transitionStatus: vi.fn(),
}))
const messages = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn() }))
const ui = vi.hoisted(() => ({
  box: { inheritAttrs: false, template: '<div v-bind="$attrs"><slot /><slot name="icon" /><slot name="footer" /></div>' },
  button: { inheritAttrs: false, template: '<button v-bind="$attrs"><slot /><slot name="icon" /></button>' },
  input: { inheritAttrs: false, props: ['value'], emits: ['update:value'], template: '<input v-bind="$attrs" :value="value" @input="$emit(\'update:value\', $event.target.value)" />' },
  select: { inheritAttrs: false, props: ['value', 'options'], emits: ['update:value', 'search'], template: '<select v-bind="$attrs" :value="value" @change="$emit(\'update:value\', $event.target.value)"><option v-for="option in options ?? []" :key="option.value" :value="option.value">{{ option.label }}</option></select>' },
}))

vi.mock('../../src/api/http', () => ({ ...http }))
vi.mock('../../src/stores/invoices', () => ({ useInvoicesStore: () => store }))
vi.mock('naive-ui', () => ({
  useMessage: () => messages,
  NAlert: ui.box, NButton: ui.button, NCard: ui.box, NCollapse: ui.box, NCollapseItem: ui.box, NDatePicker: ui.input, NDivider: ui.box, NDropdown: ui.box,
  NForm: ui.box, NFormItem: ui.box, NGrid: ui.box, NGi: ui.box, NIcon: ui.box, NInput: ui.input, NInputNumber: ui.input,
  NList: ui.box, NListItem: ui.box, NModal: ui.box, NSpace: ui.box, NSelect: ui.select, NSpin: ui.box, NSwitch: ui.input,
  NTag: ui.box, NText: ui.box, NThing: ui.box, NEmpty: ui.box,
}))
vi.mock('@vicons/ionicons5', () => ({ AddOutline: ui.box, TrashOutline: ui.box, DocumentTextOutline: ui.box, DownloadOutline: ui.box, MailOutline: ui.box, EyeOutline: ui.box }))

import InvoiceEdit from '../../src/views/invoices/InvoiceEdit.vue'

const i18n = createI18n({ legacy: false, locale: 'en', messages: { en } })

function invoice(id: string, kind: 'STANDARD' | 'CREDIT_NOTE') {
  return {
    id,
    invoice_number: `INV-${id}`,
    document_kind: kind,
    status: 'DRAFT', paid_status: 'UNPAID', customer_id: `customer-${id}`,
    reference_number: `reference-${id}`, invoice_date: '2026-08-31', due_date: '2026-09-30',
    tax_mode: 'LINE', amounts_include_vat: false, vat_treatment_id: `treatment-${id}`,
    document_vat_rate_id: null, discount_type: 'NONE', discount_value: '0', notes: `notes-${id}`,
    warranty_text: null, terms_text: null, bank_text: null, payment_terms_text: null,
    currency: 'EUR', due_amount: '10.00', total_incl_vat: '10.00', lines: [{
      product_id: null, name: `line-${id}`, description: null, quantity: '1', unit_id: null, unit_name: null,
      unit_price: '10', discount_type: 'NONE', discount_value: '0', vat_rate_id: null,
    }],
  }
}

describe('InvoiceEdit reused route', () => {
  function deferred<T>() {
    let resolve!: (value: T) => void
    let reject!: (cause: unknown) => void
    const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail })
    return { promise, resolve, reject }
  }

  it.each([
    ['invoice fetch', 'success', 'success'], ['invoice fetch', 'success', 'reject'],
    ['invoice fetch', 'reject', 'success'], ['invoice fetch', 'reject', 'reject'],
    ['document chain', 'success', 'success'], ['document chain', 'success', 'reject'],
    ['document chain', 'reject', 'success'], ['document chain', 'reject', 'reject'],
    ['customer', 'success', 'success'], ['customer', 'success', 'reject'],
    ['customer', 'reject', 'success'], ['customer', 'reject', 'reject'],
  ] as const)('keeps a reused B page authoritative when A %s completes late (%s) and B currently %s', async (lateTarget, lateOutcome, bOutcome) => {
    const late = deferred<any>()
    const bFetch = deferred<any>()
    const a = { ...invoice('A', 'STANDARD'), status: 'SENT' }
    const b = { ...invoice('B', 'STANDARD'), status: 'SENT' }
    store.fetchProductOptions.mockResolvedValue([])
    store.fetchInvoice.mockImplementation((id: string) => {
      if (id === 'B') return bFetch.promise
      return lateTarget === 'invoice fetch' ? late.promise : Promise.resolve(a)
    })
    http.get.mockImplementation((url: string) => {
      if (url === '/api/v1/vat-rates' || url.startsWith('/api/v1/vat-treatments')) return Promise.resolve({ items: [] })
      if (url === '/api/v1/document-templates' || url === '/api/v1/content-blocks' || url === '/api/v1/note-templates') return Promise.resolve([])
      if (url.startsWith('/api/v1/customers?q=')) return Promise.resolve({ items: [] })
      if (url === '/api/v1/invoices/A/document-chain') return lateTarget === 'document chain' ? late.promise : Promise.resolve({ quote_id: 'quote-A', nodes: [], relations: [], events: [], totals: {}, available_actions: [] })
      if (url === '/api/v1/customers/customer-A') return lateTarget === 'customer' ? late.promise : Promise.resolve({ id: 'customer-A', name: 'Customer A' })
      if (url === '/api/v1/invoices/B/document-chain') return Promise.resolve({ quote_id: 'quote-B', nodes: [], relations: [], events: [], totals: {}, available_actions: [] })
      if (url === '/api/v1/customers/customer-B') return Promise.resolve({ id: 'customer-B', name: 'Customer B' })
      throw new Error(`unexpected GET ${url}`)
    })
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/invoices/:id/edit', component: InvoiceEdit }] })
    await router.push('/invoices/A/edit'); await router.isReady()
    const wrapper = mount(InvoiceEdit, { global: { plugins: [router, i18n], stubs: {
      DocumentWorkflowPanel: { props: ['invoice', 'documentChain'], template: '<div data-workflow :data-invoice="invoice.id" :data-chain="documentChain?.quote_id ?? \'pending\'" />' },
      InvoicePaymentPanel: { props: ['invoiceId'], template: '<div data-payment :data-invoice="invoiceId" />' },
      DocumentSendDialog: { template: '<div />' }, PdfPreviewDialog: { template: '<div />' }, EmailLogPanel: { template: '<div />' },
    } } })
    await flushPromises()
    if (lateTarget !== 'invoice fetch') expect(wrapper.text()).toContain('INV-A')

    await router.push('/invoices/B/edit'); await flushPromises()
    // B is pending: route reset removes A's form, customer, chain, child
    // actions/payment contract, error, and page-owned loading inheritance.
    expect(wrapper.text()).not.toContain('INV-A')
    expect(wrapper.text()).not.toContain('Customer A')
    expect(wrapper.find('[data-workflow]').exists()).toBe(false)
    expect(wrapper.find('[data-payment]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('A late failure')

    if (lateOutcome === 'success') late.resolve(lateTarget === 'customer'
      ? { id: 'customer-A', name: 'Late Customer A' }
      : { ...a, quote_id: 'quote-A', nodes: [], relations: [], events: [], totals: {}, available_actions: [] })
    else late.reject(new Error('A late failure'))
    await flushPromises()
    expect(wrapper.text()).not.toContain('INV-A')
    expect(wrapper.text()).not.toContain('Late Customer A')
    expect(wrapper.text()).not.toContain('A late failure')
    expect(wrapper.find('[data-workflow]').exists()).toBe(false)

    if (bOutcome === 'success') bFetch.resolve(b)
    else bFetch.reject(new Error('B current failure'))
    await flushPromises()
    if (bOutcome === 'success') {
      expect(wrapper.text()).toContain('INV-B')
      expect(wrapper.text()).toContain('Customer B')
      expect(wrapper.find('[data-workflow]').attributes()).toMatchObject({ 'data-invoice': 'B', 'data-chain': 'quote-B' })
      expect(wrapper.find('[data-payment]').attributes()).toMatchObject({ 'data-invoice': 'B' })
      expect(wrapper.text()).not.toContain('A late failure')
    } else {
      expect(wrapper.text()).toContain('B current failure')
      expect(wrapper.find('[data-workflow]').exists()).toBe(false)
      expect(wrapper.find('[data-payment]').exists()).toBe(false)
    }
  })

  it('keeps B customer options, error, chain and loading state current when A customer completes late', async () => {
    let resolveACustomer!: (value: { id: string; name: string }) => void
    let resolveBChain!: (value: { quote_id: string | null; nodes: never[]; relations: never[]; events: never[]; totals: Record<string, never>; available_actions: never[] }) => void
    let rejectBCustomer!: (cause: unknown) => void
    const aCustomer = new Promise<{ id: string; name: string }>(resolve => { resolveACustomer = resolve })
    const bCustomer = new Promise<{ id: string; name: string }>((_resolve, reject) => { rejectBCustomer = reject })
    store.fetchProductOptions.mockResolvedValue([])
    store.fetchInvoice.mockImplementation((id: string) => Promise.resolve(invoice(id, 'STANDARD')))
    http.get.mockImplementation((url: string) => {
      if (url === '/api/v1/vat-rates' || url.startsWith('/api/v1/vat-treatments')) return Promise.resolve({ items: [] })
      if (url === '/api/v1/document-templates' || url === '/api/v1/content-blocks' || url === '/api/v1/note-templates') return Promise.resolve([])
      if (url.startsWith('/api/v1/customers?q=')) return Promise.resolve({ items: [] })
      if (url === '/api/v1/invoices/A/document-chain') return Promise.resolve({ quote_id: 'quote-A', nodes: [], relations: [], events: [], totals: {}, available_actions: [] })
      if (url === '/api/v1/customers/customer-A') return aCustomer
      if (url === '/api/v1/invoices/B/document-chain') return new Promise(resolve => { resolveBChain = resolve })
      if (url === '/api/v1/customers/customer-B') return bCustomer
      throw new Error(`unexpected GET ${url}`)
    })
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/invoices/:id/edit', component: InvoiceEdit }] })
    await router.push('/invoices/A/edit'); await router.isReady()
    const wrapper = mount(InvoiceEdit, { global: { plugins: [router, i18n], stubs: {
      DocumentWorkflowPanel: { props: ['invoice', 'documentChain'], template: '<div data-workflow :data-invoice="invoice.id" :data-chain="documentChain?.quote_id ?? \'none\'" />' },
      InvoicePaymentPanel: { template: '<div />' }, DocumentSendDialog: { template: '<div />' }, PdfPreviewDialog: { template: '<div />' }, EmailLogPanel: { template: '<div />' },
    } } })
    await flushPromises()
    expect(wrapper.find('[data-workflow]').attributes()).toMatchObject({ 'data-invoice': 'A', 'data-chain': 'quote-A' })

    await router.push('/invoices/B/edit'); await flushPromises()
    expect(wrapper.find('[data-workflow]').exists()).toBe(false)
    resolveACustomer({ id: 'customer-A', name: 'Late A' })
    await flushPromises()
    expect(wrapper.html()).not.toContain('Late A')
    resolveBChain({ quote_id: 'quote-B', nodes: [], relations: [], events: [], totals: {}, available_actions: [] })
    await flushPromises()
    expect(wrapper.find('[data-workflow]').attributes()).toMatchObject({ 'data-invoice': 'B', 'data-chain': 'quote-B' })
    rejectBCustomer(new Error('B customer failed'))
    await flushPromises()
    expect(wrapper.text()).toContain('B customer failed')
    expect(wrapper.text()).not.toContain('Late A')
  })

  it('resets A state and keeps B authoritative when A returns late, including child workflow/payment contracts', async () => {
    let resolveAChain!: (value: { quote_id: string; nodes: never[]; relations: never[]; events: never[]; totals: Record<string, never>; available_actions: never[] }) => void
    store.fetchProductOptions.mockResolvedValue([])
    store.fetchInvoice.mockImplementation((id: string) => Promise.resolve(invoice(id, id === 'A' ? 'STANDARD' : 'CREDIT_NOTE')))
    http.get.mockImplementation((url: string) => {
      if (url === '/api/v1/vat-rates' || url.startsWith('/api/v1/vat-treatments')) return Promise.resolve({ items: [] })
      if (url === '/api/v1/document-templates' || url === '/api/v1/content-blocks' || url === '/api/v1/note-templates') return Promise.resolve([])
      if (url.startsWith('/api/v1/customers?q=')) return Promise.resolve({ items: [] })
      if (url === '/api/v1/customers/customer-A') return Promise.resolve({ id: 'customer-A', name: 'Customer A', email: 'a@example.test' })
      if (url === '/api/v1/customers/customer-B') return Promise.resolve({ id: 'customer-B', name: 'Customer B', email: 'b@example.test' })
      if (url === '/api/v1/invoices/A/document-chain') return new Promise(resolve => { resolveAChain = resolve })
      if (url === '/api/v1/invoices/B/document-chain') return Promise.resolve({ quote_id: null, nodes: [], relations: [], events: [], totals: {}, available_actions: [] })
      throw new Error(`unexpected GET ${url}`)
    })
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/invoices/:id/edit', component: InvoiceEdit }] })
    await router.push('/invoices/A/edit')
    await router.isReady()
    const wrapper = mount(InvoiceEdit, {
      global: {
        plugins: [router, i18n],
        stubs: {
          DocumentWorkflowPanel: { props: ['invoice', 'documentChain'], template: '<div data-workflow :data-invoice="invoice.id" :data-customer="invoice.customer_id" :data-chain="documentChain?.quote_id ?? \'none\'" />' },
          InvoicePaymentPanel: { props: ['invoiceId'], template: '<div data-payment :data-invoice="invoiceId" />' },
          DocumentSendDialog: { template: '<div />' }, PdfPreviewDialog: { template: '<div />' }, EmailLogPanel: { template: '<div />' },
        },
      },
    })
    await flushPromises()
    expect(store.fetchInvoice).toHaveBeenCalledWith('A')
    expect(wrapper.find('[data-workflow]').exists()).toBe(false)
    expect(wrapper.find('[data-payment]').attributes()).toMatchObject({ 'data-invoice': 'A' })
    expect(wrapper.text()).toContain('INV-A')

    await router.push('/invoices/B/edit')
    await flushPromises()
    resolveAChain({ quote_id: 'quote-A', nodes: [], relations: [], events: [], totals: {}, available_actions: [] })
    await flushPromises()

    expect(store.fetchInvoice).toHaveBeenCalledWith('B')
    expect(wrapper.find('[data-workflow]').attributes()).toMatchObject({ 'data-invoice': 'B', 'data-customer': 'customer-B', 'data-chain': 'none' })
    expect(wrapper.find('[data-payment]').exists()).toBe(false)
    expect(wrapper.text()).toContain('INV-B')
    expect(wrapper.html()).not.toContain('reference-A')
    expect(wrapper.html()).not.toContain('line-A')
  })

  it('shows a current chain-read error without fake workflow actions and retries the invoice endpoint', async () => {
    let chainAttempts = 0
    store.fetchProductOptions.mockResolvedValue([])
    store.fetchInvoice.mockResolvedValue(invoice('B', 'STANDARD'))
    http.get.mockImplementation((url: string) => {
      if (url === '/api/v1/vat-rates' || url.startsWith('/api/v1/vat-treatments')) return Promise.resolve({ items: [] })
      if (url === '/api/v1/document-templates' || url === '/api/v1/content-blocks' || url === '/api/v1/note-templates') return Promise.resolve([])
      if (url.startsWith('/api/v1/customers?q=')) return Promise.resolve({ items: [] })
      if (url === '/api/v1/customers/customer-B') return Promise.resolve({ id: 'customer-B', name: 'Customer B' })
      if (url === '/api/v1/invoices/B/document-chain') {
        chainAttempts += 1
        return chainAttempts === 1
          ? Promise.reject(new Error('current chain unavailable'))
          : Promise.resolve({ quote_id: 'quote-B', nodes: [], relations: [], events: [], totals: {}, available_actions: [] })
      }
      throw new Error(`unexpected GET ${url}`)
    })
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/invoices/:id/edit', component: InvoiceEdit }] })
    await router.push('/invoices/B/edit'); await router.isReady()
    const wrapper = mount(InvoiceEdit, { global: { plugins: [router, i18n], stubs: {
      DocumentWorkflowPanel: { props: ['invoice', 'documentChain'], template: '<div data-workflow :data-invoice="invoice.id" :data-chain="documentChain.quote_id" />' },
      InvoicePaymentPanel: { template: '<div />' }, DocumentSendDialog: { template: '<div />' }, PdfPreviewDialog: { template: '<div />' }, EmailLogPanel: { template: '<div />' },
    } } })
    await flushPromises()
    expect(wrapper.text()).toContain('Could not load the document chain')
    expect(wrapper.find('[data-workflow]').exists()).toBe(false)
    await wrapper.findAll('button').find(button => button.text() === 'Retry')!.trigger('click')
    await flushPromises()
    expect(chainAttempts).toBe(2)
    expect(wrapper.find('[data-workflow]').attributes()).toMatchObject({ 'data-invoice': 'B', 'data-chain': 'quote-B' })
  })

  it('retains the last successful invoice chain and warns, then retries, after a payment refresh failure', async () => {
    let chainAttempts = 0
    store.fetchProductOptions.mockResolvedValue([])
    store.fetchInvoice.mockResolvedValue(invoice('B', 'STANDARD'))
    http.get.mockImplementation((url: string) => {
      if (url === '/api/v1/vat-rates' || url.startsWith('/api/v1/vat-treatments')) return Promise.resolve({ items: [] })
      if (url === '/api/v1/document-templates' || url === '/api/v1/content-blocks' || url === '/api/v1/note-templates') return Promise.resolve([])
      if (url.startsWith('/api/v1/customers?q=')) return Promise.resolve({ items: [] })
      if (url === '/api/v1/customers/customer-B') return Promise.resolve({ id: 'customer-B', name: 'Customer B' })
      if (url === '/api/v1/invoices/B/document-chain') {
        chainAttempts += 1
        if (chainAttempts === 2) return Promise.reject(new Error('refresh unavailable'))
        return Promise.resolve({ quote_id: `quote-B-${chainAttempts}`, nodes: [], relations: [], events: [], totals: {}, available_actions: [] })
      }
      throw new Error(`unexpected GET ${url}`)
    })
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/invoices/:id/edit', component: InvoiceEdit }] })
    await router.push('/invoices/B/edit'); await router.isReady()
    const wrapper = mount(InvoiceEdit, { global: { plugins: [router, i18n], stubs: {
      DocumentWorkflowPanel: { props: ['documentChain'], template: '<div data-workflow :data-chain="documentChain.quote_id" />' },
      InvoicePaymentPanel: { emits: ['paymentsChanged'], template: '<button data-mutate @click="$emit(\'paymentsChanged\', { paid_status: \'PARTIALLY_PAID\', status: \'SENT\', due_amount: \'5.00\' })">mutate</button>' },
      DocumentSendDialog: { template: '<div />' }, PdfPreviewDialog: { template: '<div />' }, EmailLogPanel: { template: '<div />' },
    } } })
    await flushPromises()
    expect(wrapper.find('[data-workflow]').attributes('data-chain')).toBe('quote-B-1')
    await wrapper.find('[data-mutate]').trigger('click'); await flushPromises()
    expect(wrapper.text()).toContain('payment was saved, but the document chain below may be out of date')
    expect(wrapper.find('[data-workflow]').attributes('data-chain')).toBe('quote-B-1')
    await wrapper.findAll('button').find(button => button.text() === 'Retry')!.trigger('click'); await flushPromises()
    expect(wrapper.find('[data-workflow]').attributes('data-chain')).toBe('quote-B-3')
  })
})
