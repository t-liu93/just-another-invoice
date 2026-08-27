import { defineStore } from 'pinia'
import { ref } from 'vue'
import { del, get, post, put } from '../api/http'
import { ApiError } from '../api/http'
import type { components } from '../api/schema'

type InvoicePaymentsResponse = components['schemas']['InvoicePaymentsResponse']
type QuotePaymentsResponse = components['schemas']['QuotePaymentsResponse']
type PaymentMutationResponse = components['schemas']['PaymentMutationResponse']
type PaymentRead = components['schemas']['PaymentRead']
type PaymentInput = components['schemas']['PaymentInput']
type PaymentListItem = components['schemas']['PaymentListItem']
type PaymentListResponse = components['schemas']['PaymentListResponse']

export type {
  InvoicePaymentsResponse,
  QuotePaymentsResponse,
  PaymentMutationResponse,
  PaymentRead,
  PaymentInput,
  PaymentListItem,
}

export const usePaymentsStore = defineStore('payments', () => {
  // Global payments list state (for PaymentList overview page)
  const items = ref<PaymentListItem[]>([])
  const total = ref(0)
  const loading = ref(false)
  const saving = ref(false)
  const error = ref<string | null>(null)

  const query = ref('')
  const customerIdFilter = ref<string | null>(null)
  const paymentMethodIdFilter = ref<string | null>(null)
  const dateFrom = ref<string | null>(null)
  const dateTo = ref<string | null>(null)
  const limit = ref(50)
  const offset = ref(0)
  const sortBy = ref<'payment_date' | 'created_at'>('payment_date')

  async function fetchPayments() {
    loading.value = true
    error.value = null
    try {
      const params = new URLSearchParams()
      if (query.value) params.set('q', query.value)
      if (customerIdFilter.value) params.set('customer_id', customerIdFilter.value)
      if (paymentMethodIdFilter.value) params.set('payment_method_id', paymentMethodIdFilter.value)
      if (dateFrom.value) params.set('date_from', dateFrom.value)
      if (dateTo.value) params.set('date_to', dateTo.value)
      params.set('limit', String(limit.value))
      params.set('offset', String(offset.value))
      params.set('sort_by', sortBy.value)
      const qs = params.toString()
      const url = `/api/v1/payments${qs ? '?' + qs : ''}`
      const data = await get<PaymentListResponse>(url)
      items.value = data.items
      total.value = data.total
    } catch (e: unknown) {
      error.value = e instanceof ApiError ? e.message : String(e)
    } finally {
      loading.value = false
    }
  }

  // Invoice-scoped payment operations
  async function listInvoicePayments(invoiceId: string): Promise<InvoicePaymentsResponse> {
    return await get<InvoicePaymentsResponse>(`/api/v1/invoices/${invoiceId}/payments`)
  }

  async function recordPayment(invoiceId: string, body: PaymentInput): Promise<InvoicePaymentsResponse> {
    saving.value = true
    error.value = null
    try {
      return await post<InvoicePaymentsResponse>(`/api/v1/invoices/${invoiceId}/payments`, body)
    } catch (e: unknown) {
      if (e instanceof ApiError) error.value = e.message
      else error.value = String(e)
      throw e
    } finally {
      saving.value = false
    }
  }

  async function listQuotePayments(quoteId: string): Promise<QuotePaymentsResponse> {
    return await get<QuotePaymentsResponse>(`/api/v1/quotes/${quoteId}/payments`)
  }

  async function recordQuotePayment(quoteId: string, body: PaymentInput): Promise<QuotePaymentsResponse> {
    saving.value = true
    error.value = null
    try {
      return await post<QuotePaymentsResponse>(`/api/v1/quotes/${quoteId}/payments`, body)
    } catch (e: unknown) {
      if (e instanceof ApiError) error.value = e.message
      else error.value = String(e)
      throw e
    } finally {
      saving.value = false
    }
  }

  async function updatePayment(paymentId: string, body: PaymentInput): Promise<PaymentMutationResponse> {
    saving.value = true
    error.value = null
    try {
      return await put<PaymentMutationResponse>(`/api/v1/payments/${paymentId}`, body)
    } catch (e: unknown) {
      if (e instanceof ApiError) error.value = e.message
      else error.value = String(e)
      throw e
    } finally {
      saving.value = false
    }
  }

  async function deletePayment(paymentId: string): Promise<PaymentMutationResponse> {
    saving.value = true
    error.value = null
    try {
      return await del<PaymentMutationResponse>(`/api/v1/payments/${paymentId}`)
    } catch (e: unknown) {
      if (e instanceof ApiError) error.value = e.message
      else error.value = String(e)
      throw e
    } finally {
      saving.value = false
    }
  }

  async function getPayment(paymentId: string): Promise<PaymentRead> {
    return await get<PaymentRead>(`/api/v1/payments/${paymentId}`)
  }

  return {
    items,
    total,
    loading,
    saving,
    error,
    query,
    customerIdFilter,
    paymentMethodIdFilter,
    dateFrom,
    dateTo,
    limit,
    offset,
    sortBy,
    fetchPayments,
    listInvoicePayments,
    listQuotePayments,
    recordPayment,
    recordQuotePayment,
    updatePayment,
    deletePayment,
    getPayment,
  }
})
