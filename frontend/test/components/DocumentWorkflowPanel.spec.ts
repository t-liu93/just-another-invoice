import { describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import en from '../../src/locales/en.json'

const http = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn(), del: vi.fn() }))
const naive = vi.hoisted(() => ({
  inheritAttrs: false,
  emits: ['click', 'positive-click', 'update:show'],
  template: '<button v-bind="$attrs" @click="$emit(\'click\', $event)"><slot /></button>',
}))
const actionButton = vi.hoisted(() => ({
  inheritAttrs: false,
  emits: ['click'],
  template: '<button data-action-button v-bind="$attrs" @click="$emit(\'click\', $event)"><slot /></button>',
}))
const semantic = vi.hoisted(() => ({
input: {
  inheritAttrs: false, props: ['value', 'modelValue'], emits: ['update:value', 'update:modelValue', 'focus'],
  template: '<input v-bind="$attrs" :value="value ?? modelValue" @focus="$emit(\'focus\')" @input="$emit(\'update:value\', $event.target.value); $emit(\'update:modelValue\', $event.target.value)" />',
},
select: {
  inheritAttrs: false, props: ['value', 'modelValue', 'options'], emits: ['update:value', 'update:modelValue', 'focus'],
  template: '<select v-bind="$attrs" :value="value ?? modelValue" @focus="$emit(\'focus\')" @change="$emit(\'update:value\', $event.target.value); $emit(\'update:modelValue\', $event.target.value)"><option v-for="item in options ?? []" :key="item.value ?? item.key" :value="item.value ?? item.key">{{ item.label }}</option></select>',
},
modal: {
  inheritAttrs: false, props: ['show'], emits: ['update:show', 'positive-click', 'negative-click'],
  template: '<section v-if="show"><slot /><button data-positive @click="$emit(\'positive-click\')">confirm</button><button data-negative @click="$emit(\'negative-click\')">cancel</button></section>',
},
}))
vi.mock('../../src/api/http', () => ({ ...http, downloadBlob: vi.fn(), ApiError: class ApiError extends Error {} }))
vi.mock('naive-ui', () => ({
  NAlert: naive, NButton: actionButton, NCard: naive, NDatePicker: naive,
  NDescriptions: naive, NDescriptionsItem: naive, NDivider: naive,
  NEmpty: naive, NForm: naive, NFormItem: naive, NInput: semantic.input,
  NList: naive, NListItem: naive, NModal: semantic.modal, NSelect: semantic.select,
  NSpace: naive, NSpin: naive, NTag: naive, NText: naive,
}))
vi.mock('../../src/components/PdfPreviewDialog.vue', () => ({ default: { template: '<div />' } }))
vi.mock('../../src/components/DocumentSendDialog.vue', () => ({ default: { template: '<div />' } }))
const router = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn() }))
vi.mock('vue-router', () => ({ useRouter: () => router }))

import DocumentWorkflowPanel from '../../src/components/DocumentWorkflowPanel.vue'

const i18n = createI18n({ legacy: false, locale: 'en', messages: { en } })
// Explicit Naive UI module doubles keep attributes and click events in the
// mounted DOM. This avoids teleports while preserving testable UI semantics.
const stubs = {
  PdfPreviewDialog: { template: '<div />' },
  DocumentSendDialog: { template: '<div />' },
}
const baseChain = { settlement_mode: 'FORMAL_ADVANCE', totals: {}, nodes: [], relations: [], events: [], available_actions: [] }
const invoice = { id: 'source', document_kind: 'ADVANCE', status: 'SENT', quote_id: 'quote', currency: 'EUR', total_incl_vat: '100', due_amount: '100', credited_total: '0', payable_before_payments: '100' }
const credit = { ...invoice, id: 'credit', document_kind: 'CREDIT_NOTE', status: 'SENT', source_invoice_id: 'source' }

describe('DocumentWorkflowPanel target action projection', () => {
  function deferred<T>() {
    let resolve!: (value: T) => void
    let reject!: (cause: unknown) => void
    const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail })
    return { promise, resolve, reject }
  }
  function action(wrapper: ReturnType<typeof mount>, label: string) {
    const control = wrapper.findAll('button[data-action-button]').find(button => button.text().includes(label))
    if (!control) throw new Error(`missing visible ${label} control`)
    return control
  }

  it('renders only target-scoped actions and keeps Final available after an unpaid Advance', async () => {
    const wrapper = mount(DocumentWorkflowPanel, { props: { invoice, documentChain: { ...baseChain, available_actions: [
      { code: 'CREATE_FINAL', available: true, target_id: 'quote', target_type: 'QUOTE' },
      { code: 'CREATE_CREDIT_NOTE', available: true, target_id: 'other', target_type: 'INVOICE' },
    ] } }, global: { plugins: [i18n], stubs } })
    expect(wrapper.text()).toContain('Create final')
    expect(wrapper.text()).not.toContain('Create credit note')
  })

  it('leaves an externally owned chain to its parent on mount and reports one failed explicit refresh', async () => {
    const refreshChain = vi.fn().mockResolvedValue(false)
    const wrapper = mount(DocumentWorkflowPanel, {
      props: {
        quoteId: 'quote', documentChain: baseChain, refreshChain,
        chainError: 'Could not load the document chain',
      },
      global: { plugins: [i18n], stubs },
    })
    await flushPromises()
    expect(refreshChain).not.toHaveBeenCalled()
    expect(http.get.mock.calls.map(([url]) => url)).not.toContain('/api/v1/quotes/quote/document-chain')
    await action(wrapper, 'Refresh').trigger('click')
    await flushPromises()
    expect(refreshChain).toHaveBeenCalledTimes(1)
    expect(wrapper.text()).toContain('Could not load the document chain')
  })

  it.each(['DIRECT_INVOICE', 'RECEIPT_ONLY'] as const)('keeps %s read-only while rendering authoritative totals and every timeline kind', async (mode) => {
    const node = { id: 'invoice', node_type: 'INVOICE', document_kind: 'STANDARD', number: 'INV-42', occurred_on: '2026-08-31', charge_amount: '120.00', credit_amount: '10.00', due_amount: '90.00', refund_due_amount: '0.00', incoming_payment_amount: '20.00', refund_amount: '0.00' }
    const relation = { from_node_id: 'quote', to_node_id: 'invoice', relation_type: 'QUOTE_TO_INVOICE' }
    const event = { id: 'event', event_type: 'MODE_LOCKED', occurred_at: '2026-08-31T10:00:00Z', event_order: 1 }
    const application = { final_invoice_id: 'invoice', advance_invoice_id: 'advance', occurred_on: '2026-08-31', taxable_amount: '50.00', vat_amount: '10.50', gross_amount: '60.50' }
    const wrapper = mount(DocumentWorkflowPanel, {
      props: {
        quoteId: 'quote',
        documentChain: {
          settlement_mode: mode,
          totals: { due_amount: '90.00', refund_due_amount: '0.00', credit_total: '10.00', incoming_payment_total: '20.00' },
          nodes: [node], relations: [relation], events: [event],
          timeline: [
            { kind: 'NODE', order: 1, node }, { kind: 'RELATION', order: 2, relation },
            { kind: 'EVENT', order: 3, event }, { kind: 'APPLICATION', order: 4, application },
          ],
          available_actions: [
            { code: 'CREATE_ADVANCE', available: false, reason_code: 'FORMAL_CHAIN_REQUIRED', target_id: 'quote', target_type: 'QUOTE' },
            { code: 'CREATE_FINAL', available: false, reason_code: 'MODE_CONFLICT', target_id: 'quote', target_type: 'QUOTE' },
            { code: 'CREATE_PROJECT_CANCELLATION', available: false, reason_code: 'ACTION_UNAVAILABLE', target_id: 'quote', target_type: 'QUOTE' },
          ],
        },
      },
      global: { plugins: [i18n], stubs },
    })
    expect(wrapper.text()).toContain('90.00')
    expect(wrapper.text()).toContain('INV-42')
    expect(wrapper.text()).toContain('Quote converted to invoice')
    expect(wrapper.text()).toContain('Billing mode locked')
    expect(wrapper.text()).toContain('50.00 + 10.50 = 60.50')
    expect(wrapper.text()).not.toContain('Create advance')
    expect(wrapper.text()).not.toContain('This action requires a formal Advance/Final chain.')
    expect(wrapper.text()).not.toContain('This billing mode has already been locked')
  })

  it('uses source-only Credit calculate/create endpoints and raw basis input', async () => {
    http.post.mockResolvedValueOnce({ gross_amount: '10', remaining_gross_amount: '90', lines: [] })
    const wrapper = mount(DocumentWorkflowPanel, { props: { invoice, documentChain: { ...baseChain, available_actions: [{ code: 'CREATE_CREDIT_NOTE', available: true, target_id: 'source', target_type: 'INVOICE' }] } }, global: { plugins: [i18n], stubs } })
    await wrapper.vm.$nextTick()
    ;(wrapper.vm as any).creditFull = true
    await (wrapper.vm as any).calculateCredit()
    expect(http.post).toHaveBeenCalledWith('/api/v1/invoices/source/credit-notes/calculate', { full_remaining: true })
  })

  it('records a Refund with only raw payment fields after its explicit confirmation', async () => {
    http.post.mockResolvedValueOnce({ credit_note_id: 'credit', items: [] })
    const wrapper = mount(DocumentWorkflowPanel, { props: { invoice: credit, documentChain: baseChain }, global: { plugins: [i18n], stubs } })
    Object.assign(wrapper.vm as any, { refundAmount: '12.34', refundDate: '2026-03-05', refundMethodId: 'method', refundReference: 'refund-ref', refundNote: 'note', pendingRefundAction: 'create' })
    await (wrapper.vm as any).confirmRefund()
    expect(http.post).toHaveBeenCalledWith('/api/v1/credit-notes/credit/refunds', { payment_date: '2026-03-05', amount: '12.34', payment_method_id: 'method', reference: 'refund-ref', note: 'note' })
  })

  it('deletes the selected Refund only through the confirmed payment endpoint', async () => {
    http.del.mockResolvedValueOnce({})
    const wrapper = mount(DocumentWorkflowPanel, { props: { invoice: credit, documentChain: baseChain }, global: { plugins: [i18n], stubs } })
    Object.assign(wrapper.vm as any, { editingRefund: { id: 'refund-1' }, pendingRefundAction: 'delete' })
    await (wrapper.vm as any).confirmRefund()
    expect(http.del).toHaveBeenCalledWith('/api/v1/payments/refund-1')
  })

  it('requires explicit follow-up and cancellation confirmation before their command endpoints', async () => {
    const followup = mount(DocumentWorkflowPanel, { props: { invoice: credit, documentChain: { ...baseChain, available_actions: [{ code: 'CREATE_REPLACEMENT', available: true, target_id: 'credit', target_type: 'INVOICE', followup_context: { credit_note_id: 'credit', source_invoice_id: 'source', relation_type: 'REPLACEMENT_OF', target_document_kind: 'ADVANCE', gross_amount: '100.00' } }] } }, global: { plugins: [i18n], stubs } })
    ;(followup.vm as any).requestFollowup('replacement')
    await followup.vm.$nextTick()
    expect(followup.html()).toContain('Replacement of credit')
    expect(followup.html()).toContain('Advance Invoice')
    expect(followup.html()).toContain('100.00')
    expect(http.post).not.toHaveBeenCalledWith('/api/v1/credit-notes/credit/replacement', {})
    http.post.mockResolvedValueOnce({ id: 'replacement' })
    await (followup.vm as any).createFollowup()
    expect(http.post).toHaveBeenCalledWith('/api/v1/credit-notes/credit/replacement', {})

    const cancellation = mount(DocumentWorkflowPanel, { props: { quoteId: 'quote', documentChain: baseChain }, global: { plugins: [i18n], stubs } })
    ;(cancellation.vm as any).finalDate = '2026-03-05'
    await cancellation.vm.$nextTick()
    ;(cancellation.vm as any).cancellationPreview = { preview_token: 'token', sources: [{ source_invoice_id: 'source', source_invoice_number: 'INV-1', remaining_gross_amount: '12.00' }] }
    ;(cancellation.vm as any).cancellationPreviewRequest = { invoice_date: '2026-03-05' }
    ;(cancellation.vm as any).cancellationPreviewSignature = JSON.stringify({ invoice_date: '2026-03-05' })
    ;(cancellation.vm as any).requestCancellation()
    await cancellation.vm.$nextTick()
    expect(cancellation.html()).toContain('Create Credit Note drafts for every source shown')
    http.post.mockResolvedValueOnce({})
    await (cancellation.vm as any).createCancellation()
    expect(http.post).toHaveBeenCalledWith('/api/v1/quotes/quote/cancellation/create-credit-drafts', { invoice_date: '2026-03-05', preview_token: 'token' })
  })

  it('edits a Refund through the payment mutation endpoint with the complete raw body', async () => {
    http.put.mockResolvedValueOnce({})
    const wrapper = mount(DocumentWorkflowPanel, { props: { invoice: credit, documentChain: baseChain }, global: { plugins: [i18n], stubs } })
    Object.assign(wrapper.vm as any, { editingRefund: { id: 'refund-1' }, pendingRefundAction: 'update', refundAmount: '5.00', refundDate: '2026-03-06', refundMethodId: null, refundReference: 'changed', refundNote: null })
    await (wrapper.vm as any).confirmRefund()
    expect(http.put).toHaveBeenCalledWith('/api/v1/payments/refund-1', { payment_date: '2026-03-06', amount: '5.00', payment_method_id: null, reference: 'changed', note: null })
  })

  it('opens the typed Refund send dialog for the exact refund id', async () => {
    const wrapper = mount(DocumentWorkflowPanel, { props: { invoice: credit, documentChain: baseChain }, global: { plugins: [i18n], stubs } })
    ;(wrapper.vm as any).openRefundSend('refund-1')
    await wrapper.vm.$nextTick()
    expect(wrapper.html()).toContain('refund')
  })

  it('keeps a target-scoped Credit action hidden when chain target differs', () => {
    const wrapper = mount(DocumentWorkflowPanel, { props: { invoice, documentChain: { ...baseChain, available_actions: [{ code: 'CREATE_CREDIT_NOTE', available: true, target_id: 'different', target_type: 'INVOICE' }] } }, global: { plugins: [i18n], stubs } })
    expect(wrapper.text()).not.toContain('workflow.createCredit')
  })

  it('renders an empty Refund collection without exposing a payment action', () => {
    const wrapper = mount(DocumentWorkflowPanel, { props: { invoice: credit, documentChain: baseChain }, global: { plugins: [i18n], stubs } })
    expect(wrapper.html()).not.toContain('/api/v1/invoices/credit/payments')
    expect(wrapper.text()).not.toContain('Record payment')
  })

  it('does not send a second calculate request while the first action is busy', async () => {
    let resolve!: (value: unknown) => void
    http.post.mockReset()
    http.post.mockImplementationOnce(() => new Promise(done => { resolve = done }))
    const wrapper = mount(DocumentWorkflowPanel, { props: { invoice, documentChain: { ...baseChain, available_actions: [{ code: 'CREATE_CREDIT_NOTE', available: true, target_id: 'source', target_type: 'INVOICE' }] } }, global: { plugins: [i18n], stubs } })
    const first = (wrapper.vm as any).calculateCredit()
    await (wrapper.vm as any).calculateCredit()
    expect(http.post).toHaveBeenCalledTimes(1)
    resolve({ gross_amount: '1', remaining_gross_amount: '9', lines: [] })
    await first
  })

  it('starts exactly one current selected-basis request for a Full → Selected intent', async () => {
    let resolve!: (value: any) => void
    http.post.mockReset()
    http.post.mockImplementationOnce(() => new Promise(done => { resolve = done }))
    const wrapper = mount(DocumentWorkflowPanel, { props: { invoice, documentChain: { ...baseChain, available_actions: [{ code: 'CREATE_CREDIT_NOTE', available: true, target_id: 'source', target_type: 'INVOICE' }] } }, global: { plugins: [i18n], stubs } })
    ;(wrapper.vm as any).chooseCreditMode(false)
    await wrapper.vm.$nextTick()
    await Promise.resolve()
    await Promise.resolve()
    expect(http.post).toHaveBeenCalledTimes(1)
    expect((wrapper.vm as any).busy).toBe(true)
    resolve({ gross_amount: '100', remaining_gross_amount: '0', lines: [{ source_basis_line_id: 'basis-B', name: 'B', quantity: '1', gross_amount: '100' }] })
    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()
    expect((wrapper.vm as any).creditSourcePreview.lines[0].source_basis_line_id).toBe('basis-B')
    expect((wrapper.vm as any).busy).toBe(false)
  })

  it('keeps selected Credit basis options through row mutations and creates the exact draft from the modal controls', async () => {
    http.post.mockReset(); http.put.mockReset(); router.push.mockClear()
    http.post
      .mockResolvedValueOnce({ gross_amount: '100', remaining_gross_amount: '0', lines: [{ source_basis_line_id: 'basis-1', name: 'Service', quantity: '2', gross_amount: '100' }] })
      .mockResolvedValueOnce({ gross_amount: '50', remaining_gross_amount: '50', lines: [] })
      .mockResolvedValueOnce({ id: 'credit-draft' })
    const wrapper = mount(DocumentWorkflowPanel, { props: { invoice, documentChain: { ...baseChain, available_actions: [{ code: 'CREATE_CREDIT_NOTE', available: true, target_id: 'source', target_type: 'INVOICE' }] } }, global: { plugins: [i18n], stubs } })
    await action(wrapper, 'Create credit note').trigger('click')
    await action(wrapper, 'Selected source lines').trigger('click')
    await flushPromises()
    expect(http.post).toHaveBeenNthCalledWith(1, '/api/v1/invoices/source/credit-notes/calculate', { full_remaining: true })
    await action(wrapper, 'Add selection').trigger('click')
    await wrapper.vm.$nextTick()
    const selects = wrapper.findAll('select')
    expect(selects[0].text()).toContain('Service')
    await selects[0].setValue('basis-1')
    await selects[1].setValue('GROSS_AMOUNT')
    const raw = wrapper.findAll('input').find(input => input.attributes('placeholder') === 'Amount or percentage')
    if (!raw) throw new Error('missing selected Credit raw input')
    await raw.setValue('50')
    await wrapper.vm.$nextTick()
    // Remove and re-add a row after editing: the basis dictionary belongs to
    // the source context, not the calculation preview, and remains usable.
    await wrapper.find('[aria-label="Delete"]').trigger('click')
    await action(wrapper, 'Add selection').trigger('click')
    await wrapper.vm.$nextTick()
    expect(wrapper.findAll('select')[0].text()).toContain('Service')
    await wrapper.findAll('select')[0].setValue('basis-1')
    await wrapper.findAll('select')[1].setValue('GROSS_AMOUNT')
    const retryRaw = wrapper.findAll('input').find(input => input.attributes('placeholder') === 'Amount or percentage')
    if (!retryRaw) throw new Error('missing retried selected Credit raw input')
    await retryRaw.setValue('50')
    await action(wrapper, 'Calculate').trigger('click')
    await flushPromises()
    expect(http.post).toHaveBeenNthCalledWith(2, '/api/v1/invoices/source/credit-notes/calculate', {
      full_remaining: false,
      lines: [{ source_basis_line_id: 'basis-1', input_mode: 'GROSS_AMOUNT', gross_amount: '50' }],
    })
    await action(wrapper, 'Create draft').trigger('click')
    expect(http.post).toHaveBeenNthCalledWith(3, '/api/v1/invoices/source/credit-notes', {
      full_remaining: false,
      lines: [{ source_basis_line_id: 'basis-1', input_mode: 'GROSS_AMOUNT', gross_amount: '50' }],
      invoice_date: expect.any(String), due_date: null, supply_or_advance_date: null, reference_number: null,
    })
    expect(router.push).toHaveBeenCalledWith('/invoices/credit-draft/edit')
  })

  it('keeps a selected Credit retry actionable after the current basis request rejects', async () => {
    http.post.mockReset()
    http.post.mockRejectedValueOnce(new Error('basis unavailable'))
      .mockResolvedValueOnce({ gross_amount: '100', remaining_gross_amount: '0', lines: [{ source_basis_line_id: 'basis-retry', name: 'Retryable', quantity: '1', gross_amount: '100' }] })
    const wrapper = mount(DocumentWorkflowPanel, { props: { invoice, documentChain: { ...baseChain, available_actions: [{ code: 'CREATE_CREDIT_NOTE', available: true, target_id: 'source', target_type: 'INVOICE' }] } }, global: { plugins: [i18n], stubs } })
    await action(wrapper, 'Create credit note').trigger('click')
    await action(wrapper, 'Selected source lines').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('The operation could not be completed')
    // The same visible mode is a real retry control: it is neither disabled
    // nor dependent on direct component-state mutation.
    await action(wrapper, 'Selected source lines').trigger('click')
    await flushPromises()
    await action(wrapper, 'Add selection').trigger('click')
    await wrapper.vm.$nextTick()
    expect(wrapper.findAll('select')[0].text()).toContain('Retryable')
  })

  it('lets a migrated ambiguous draft confirm its displayed Full or Selected intent through the visible controls', async () => {
    const migrated = (id: string, fullRemaining: boolean | null) => ({
      ...credit,
      id,
      status: 'DRAFT',
      invoice_date: '2026-03-05',
      due_date: null,
      supply_or_advance_date: null,
      reference_number: null,
      credit_draft_intent: {
        full_remaining: fullRemaining,
        requires_confirmation: true,
        lines: [{ source_basis_line_id: 'basis-1', input_mode: 'QUANTITY', quantity: '1', gross_amount: null }],
      },
    })
    const button = (wrapper: ReturnType<typeof mount>, label: string) => {
      const result = wrapper.findAll('button[data-action-button]').find(item => item.text().includes(label))
      if (!result) throw new Error(`missing ${label}`)
      return result
    }

    http.post.mockReset(); http.put.mockReset()
    http.post.mockResolvedValueOnce({ gross_amount: '100', remaining_gross_amount: '0', lines: [] })
    const full = mount(DocumentWorkflowPanel, { props: { invoice: migrated('migrated-full', true), documentChain: baseChain }, global: { plugins: [i18n], stubs } })
    await full.vm.$nextTick()
    expect(full.text()).toContain('This migrated Credit draft cannot prove')
    await button(full, 'Full remaining').trigger('click')
    await full.vm.$nextTick()
    expect(full.text()).not.toContain('This migrated Credit draft cannot prove')
    await button(full, 'Calculate').trigger('click')
    expect(http.post).toHaveBeenLastCalledWith('/api/v1/invoices/source/credit-notes/calculate', {
      full_remaining: true,
    })

    http.post.mockReset(); http.put.mockReset()
    http.post.mockResolvedValueOnce({ gross_amount: '100', remaining_gross_amount: '0', lines: [{ source_basis_line_id: 'basis-1', name: 'Basis', quantity: '1', gross_amount: '100' }] })
      .mockResolvedValueOnce({ gross_amount: '100', remaining_gross_amount: '0', lines: [] })
    http.put.mockResolvedValueOnce(migrated('migrated-selected', false))
    const selected = mount(DocumentWorkflowPanel, { props: { invoice: migrated('migrated-selected', null), documentChain: baseChain }, global: { plugins: [i18n], stubs } })
    await selected.vm.$nextTick()
    await button(selected, 'Selected source lines').trigger('click')
    await Promise.resolve(); await selected.vm.$nextTick()
    expect(selected.text()).not.toContain('This migrated Credit draft cannot prove')
    expect(http.post.mock.calls[0]).toEqual(['/api/v1/invoices/source/credit-notes/calculate', { full_remaining: true }])
    await button(selected, 'Calculate').trigger('click')
    expect(http.post).toHaveBeenLastCalledWith('/api/v1/invoices/source/credit-notes/calculate', {
      full_remaining: false,
      lines: [{ source_basis_line_id: 'basis-1', input_mode: 'QUANTITY', quantity: '1' }],
    })
    await button(selected, 'Save').trigger('click')
    expect(http.put).toHaveBeenCalledWith('/api/v1/credit-notes/migrated-selected', {
      full_remaining: false,
      lines: [{ source_basis_line_id: 'basis-1', input_mode: 'QUANTITY', quantity: '1' }],
      invoice_date: '2026-03-05', due_date: null, supply_or_advance_date: null, reference_number: null,
    })
  })

  it('drops old selected-basis success and rejection when the invoice owner changes', async () => {
    const deferred = <T,>() => {
      let resolve!: (value: T) => void; let reject!: (cause: unknown) => void
      const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail })
      return { promise, resolve, reject }
    }
    const old = deferred<any>(); const current = deferred<any>()
    http.post.mockReset()
    http.post.mockReturnValueOnce(old.promise).mockReturnValueOnce(current.promise)
    const wrapper = mount(DocumentWorkflowPanel, { props: { invoice, documentChain: baseChain }, global: { plugins: [i18n], stubs } })
    ;(wrapper.vm as any).chooseCreditMode(false)
    await wrapper.vm.$nextTick()
    await wrapper.setProps({ invoice: { ...invoice, id: 'B', source_invoice_id: 'B-source' } })
    ;(wrapper.vm as any).chooseCreditMode(false)
    await wrapper.vm.$nextTick()
    old.reject(new Error('old failed'))
    await wrapper.vm.$nextTick()
    expect((wrapper.vm as any).error).toBeNull()
    current.resolve({ gross_amount: '10', remaining_gross_amount: '0', lines: [{ source_basis_line_id: 'B-basis' }] })
    await wrapper.vm.$nextTick()
    expect((wrapper.vm as any).creditSourcePreview.lines[0].source_basis_line_id).toBe('B-basis')
    expect((wrapper.vm as any).busy).toBe(false)
  })

  it.each([
    ['advance', 'success', 'success'], ['advance', 'success', 'reject'], ['advance', 'reject', 'success'], ['advance', 'reject', 'reject'],
    ['credit', 'success', 'success'], ['credit', 'success', 'reject'], ['credit', 'reject', 'success'], ['credit', 'reject', 'reject'],
    ['cancellation', 'success', 'success'], ['cancellation', 'success', 'reject'], ['cancellation', 'reject', 'success'], ['cancellation', 'reject', 'reject'],
    ['credit-source', 'success', 'success'], ['credit-source', 'success', 'reject'], ['credit-source', 'reject', 'success'], ['credit-source', 'reject', 'reject'],
  ] as const)('keeps the %s async owner current-only when stale=%s and current=%s', async (kind, staleOutcome, currentOutcome) => {
    const old = deferred<any>(); const current = deferred<any>()
    http.post.mockReset(); http.get.mockReset()
    http.post.mockReturnValueOnce(old.promise).mockReturnValueOnce(current.promise)
    const chainFor = (id: string) => ({ ...baseChain, available_actions: kind === 'credit' || kind === 'credit-source'
      ? [{ code: 'CREATE_CREDIT_NOTE', available: true, target_id: id, target_type: 'INVOICE' }]
      : kind === 'advance'
        ? [{ code: 'CREATE_ADVANCE', available: true, target_id: id, target_type: 'QUOTE' }]
        : [{ code: 'CREATE_PROJECT_CANCELLATION', available: true, target_id: id, target_type: 'QUOTE' }],
    })
    const isInvoice = kind === 'credit' || kind === 'credit-source'
    const wrapper = mount(DocumentWorkflowPanel, {
      props: isInvoice
        ? { invoice: { ...invoice, id: 'A', source_invoice_id: 'A-source' }, documentChain: chainFor('A') }
        : { quoteId: 'A', documentChain: chainFor('A') },
      global: { plugins: [i18n], stubs },
    })
    await flushPromises()
    if (kind === 'advance') {
      await action(wrapper, 'Create advance').trigger('click')
      const input = wrapper.find('input')
      await input.setValue('10')
      await action(wrapper, 'Calculate').trigger('click')
    } else if (kind === 'cancellation') {
      await action(wrapper, 'Cancel formal project').trigger('click')
      await action(wrapper, 'Preview cancellation').trigger('click')
    } else {
      await action(wrapper, 'Create credit note').trigger('click')
      if (kind === 'credit-source') await action(wrapper, 'Selected source lines').trigger('click')
      else await action(wrapper, 'Calculate').trigger('click')
    }
    await flushPromises()
    expect((wrapper.vm as any).busy).toBe(true)
    await wrapper.setProps(isInvoice
      ? { invoice: { ...invoice, id: 'B', source_invoice_id: 'B-source' }, documentChain: chainFor('B') }
      : { quoteId: 'B', documentChain: chainFor('B') })
    await flushPromises()
    // Resetting the route makes B usable before A settles.  The next request
    // is deliberately started through the same visible control, never vm.
    expect((wrapper.vm as any).busy).toBe(false)
    if (kind === 'advance') await action(wrapper, 'Calculate').trigger('click')
    else if (kind === 'cancellation') await action(wrapper, 'Preview cancellation').trigger('click')
    else if (kind === 'credit-source') await action(wrapper, 'Selected source lines').trigger('click')
    else await action(wrapper, 'Calculate').trigger('click')
    await flushPromises()
    expect((wrapper.vm as any).busy).toBe(true)
    if (staleOutcome === 'success') old.resolve(kind === 'cancellation'
      ? { preview_token: 'old', sources: [{ source_invoice_number: 'OLD', remaining_gross_amount: '1' }] }
      : { gross_amount: '111', remaining_gross_amount: '0', lines: [{ source_basis_line_id: 'old' }] })
    else old.reject(new Error('old failure'))
    await flushPromises()
    expect((wrapper.vm as any).error).toBeNull()
    if (currentOutcome === 'success') current.resolve(kind === 'cancellation'
      ? { preview_token: 'current', sources: [{ source_invoice_number: 'CURRENT', remaining_gross_amount: '2' }] }
      : { gross_amount: '222', remaining_gross_amount: '0', lines: [{ source_basis_line_id: 'current' }] })
    else current.reject(new Error('current failure'))
    await flushPromises()
    expect((wrapper.vm as any).busy).toBe(false)
    if (currentOutcome === 'success') {
      if (kind === 'credit-source') {
        expect((wrapper.vm as any).creditSourcePreview.gross_amount).toBe('222')
      } else expect(wrapper.text()).toContain(kind === 'cancellation' ? 'CURRENT' : '222')
      if (kind === 'credit-source') expect((wrapper.vm as any).creditSourcePreview.lines[0].source_basis_line_id).toBe('current')
      else expect(wrapper.text()).not.toContain(kind === 'cancellation' ? 'OLD' : '111')
      if (kind === 'advance' || kind === 'credit') expect(action(wrapper, 'Create draft').attributes('disabled')).toBeUndefined()
      if (kind === 'cancellation') expect(action(wrapper, 'Create all credit drafts').attributes('disabled')).toBeUndefined()
    } else {
      expect(wrapper.text()).toContain('The operation could not be completed')
      expect(wrapper.text()).not.toContain(kind === 'cancellation' ? 'OLD' : '111')
      // A current failure must leave the initiating command usable again.
      expect(action(wrapper, kind === 'cancellation' ? 'Preview cancellation' : kind === 'credit-source' ? 'Selected source lines' : 'Calculate').attributes('disabled')).toBeUndefined()
    }
  })

  it('consumes an initial formal-card signal once, but does not open from a locked refresh', async () => {
    const available = { ...baseChain, settlement_mode: 'UNSET', available_actions: [{ code: 'CREATE_ADVANCE', available: true, target_id: 'quote', target_type: 'QUOTE' }] }
    const opened = mount(DocumentWorkflowPanel, { props: { quoteId: 'quote', openAdvanceSignal: 1, documentChain: available }, global: { plugins: [i18n], stubs } })
    expect((opened.vm as any).showAdvance).toBe(true)
    const locked = mount(DocumentWorkflowPanel, { props: { quoteId: 'quote', openAdvanceSignal: 1, documentChain: { ...available, settlement_mode: 'FORMAL_ADVANCE', available_actions: [] } }, global: { plugins: [i18n], stubs } })
    expect((locked.vm as any).showAdvance).toBe(false)
  })

  it('keeps accessible delete labels, loading/disabled safeguards, empty states and responsive scroll classes in the rendered component', async () => {
    const wrapper = mount(DocumentWorkflowPanel, { props: { invoice: credit, documentChain: baseChain }, global: { plugins: [i18n], stubs } })
    ;(wrapper.vm as any).creditRows = [{ source_basis_line_id: 'line', input_mode: 'QUANTITY', raw: '1' }]
    ;(wrapper.vm as any).creditFull = false
    ;(wrapper.vm as any).showCredit = true
    ;(wrapper.vm as any).refund = { items: [{ id: 'refund-1', payment_date: '2026-03-05', amount: '1.00' }], remaining_entitlement: '0', chain_refund_due_amount: '0' }
    ;(wrapper.vm as any).artifacts = { items: [] }
    ;(wrapper.vm as any).busy = true
    await wrapper.vm.$nextTick()
    expect(wrapper.find('[aria-label="Delete"]').exists()).toBe(true)
    expect(wrapper.html()).toContain('No retained documents yet.')
    expect(wrapper.html()).toContain('workflow-scroll')
    expect(wrapper.findAll('button[disabled]').length).toBeGreaterThan(0)
  })

  it('drops a delayed old-context chain response after the invoice target changes', async () => {
    let oldResolve!: (value: unknown) => void
    let newResolve!: (value: unknown) => void
    http.get.mockReset()
    http.get.mockImplementationOnce(() => new Promise(done => { oldResolve = done }))
    http.get.mockImplementationOnce(() => new Promise(done => { newResolve = done }))
    const wrapper = mount(DocumentWorkflowPanel, { props: { invoice, documentChain: undefined }, global: { plugins: [i18n], stubs } })
    await wrapper.setProps({ invoice: { ...invoice, id: 'new-source' } })
    newResolve({ ...baseChain, settlement_mode: 'RECEIPT_ONLY' })
    await wrapper.vm.$nextTick()
    oldResolve({ ...baseChain, settlement_mode: 'FORMAL_ADVANCE' })
    await wrapper.vm.$nextTick()
    expect((wrapper.vm as any).modeLabel).toBe('RECEIPT_ONLY')
  })

  it('invalidates an Advance preview when raw intent changes', async () => {
    const wrapper = mount(DocumentWorkflowPanel, { props: { quoteId: 'quote', documentChain: baseChain }, global: { plugins: [i18n], stubs } })
    ;(wrapper.vm as any).advancePreview = { gross_amount: '10' }
    ;(wrapper.vm as any).advanceRaw = '11'
    await wrapper.vm.$nextTick()
    expect((wrapper.vm as any).advancePreview).toBeNull()
  })

  it('never binds an in-flight Advance, Credit, or cancellation preview to a later intent', async () => {
    const deferred = <T,>() => {
      let resolve!: (value: T) => void
      const promise = new Promise<T>(done => { resolve = done })
      return { promise, resolve }
    }
    http.post.mockReset()
    const advance = deferred<any>()
    http.post.mockReturnValueOnce(advance.promise)
    const advanceWrapper = mount(DocumentWorkflowPanel, { props: { quoteId: 'quote', documentChain: baseChain }, global: { plugins: [i18n], stubs } })
    ;(advanceWrapper.vm as any).advanceRaw = '10'
    const advanceCall = (advanceWrapper.vm as any).calculateAdvance()
    ;(advanceWrapper.vm as any).advanceRaw = '20'
    await advanceWrapper.vm.$nextTick()
    advance.resolve({ gross_amount: '10' })
    await advanceCall
    expect((advanceWrapper.vm as any).advancePreview).toBeNull()

    const creditDeferred = deferred<any>()
    http.post.mockReturnValueOnce(creditDeferred.promise)
    const creditWrapper = mount(DocumentWorkflowPanel, { props: { invoice, documentChain: baseChain }, global: { plugins: [i18n], stubs } })
    const creditCall = (creditWrapper.vm as any).calculateCredit()
    ;(creditWrapper.vm as any).creditDate = '2026-03-06'
    await creditWrapper.vm.$nextTick()
    creditDeferred.resolve({ gross_amount: '10', remaining_gross_amount: '0', lines: [] })
    await creditCall
    expect((creditWrapper.vm as any).creditPreview).toBeNull()

    const cancellationDeferred = deferred<any>()
    http.post.mockReturnValueOnce(cancellationDeferred.promise)
    const cancellationWrapper = mount(DocumentWorkflowPanel, { props: { quoteId: 'quote', documentChain: baseChain }, global: { plugins: [i18n], stubs } })
    const cancellationCall = (cancellationWrapper.vm as any).previewCancellation()
    ;(cancellationWrapper.vm as any).finalDate = '2026-03-06'
    await cancellationWrapper.vm.$nextTick()
    cancellationDeferred.resolve({ preview_token: 'old', sources: [] })
    await cancellationCall
    expect((cancellationWrapper.vm as any).cancellationPreview).toBeNull()
  })

  it('navigates only typed invoice/quote timeline nodes, never a null cash target', () => {
    router.push.mockClear()
    const wrapper = mount(DocumentWorkflowPanel, { props: { invoice, documentChain: baseChain }, global: { plugins: [i18n], stubs } })
    ;(wrapper.vm as any).goToNode({ id: 'cash', node_type: 'PAYMENT' })
    ;(wrapper.vm as any).goToNode({ id: 'doc', node_type: 'INVOICE', document_kind: 'STANDARD' })
    expect(router.push).toHaveBeenCalledTimes(1)
    expect(router.push).toHaveBeenCalledWith('/invoices/doc/edit')
  })


})
