/**
 * Dictionaries store – manages payment_method, expense_category, and unit
 * dictionaries (M4 step 2).
 */

import { defineStore } from 'pinia'
import { ref } from 'vue'
import { ApiError, del, get, post, put } from '../api/http'
import type { components } from '../api/schema'

type PaymentMethodRead = components['schemas']['PaymentMethodRead']
type PaymentMethodWrite = components['schemas']['PaymentMethodWrite']
type PaymentMethodListResponse = components['schemas']['PaymentMethodListResponse']

type ExpenseCategoryRead = components['schemas']['ExpenseCategoryRead']
type ExpenseCategoryWrite = components['schemas']['ExpenseCategoryWrite']
type ExpenseCategoryListResponse = components['schemas']['ExpenseCategoryListResponse']

type UnitRead = components['schemas']['UnitRead']
type UnitWrite = components['schemas']['UnitWrite']
type UnitListResponse = components['schemas']['UnitListResponse']

export const usePaymentMethodStore = defineStore('paymentMethods', () => {
  const items = ref<PaymentMethodRead[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)

  async function fetchAll() {
    loading.value = true
    error.value = null
    try {
      const data = await get<PaymentMethodListResponse>('/api/v1/payment-methods')
      items.value = data.items
    } catch (e: unknown) {
      error.value = e instanceof ApiError ? e.message : String(e)
    } finally {
      loading.value = false
    }
  }

  async function create(data: PaymentMethodWrite): Promise<PaymentMethodRead> {
    const result = await post<PaymentMethodRead>('/api/v1/payment-methods', data)
    await fetchAll()
    return result
  }

  async function update(id: string, data: PaymentMethodWrite): Promise<PaymentMethodRead> {
    const result = await put<PaymentMethodRead>(`/api/v1/payment-methods/${id}`, data)
    await fetchAll()
    return result
  }

  async function remove(id: string): Promise<void> {
    await del(`/api/v1/payment-methods/${id}`)
    await fetchAll()
  }

  return { items, loading, error, fetchAll, create, update, remove }
})

export const useExpenseCategoryStore = defineStore('expenseCategories', () => {
  const items = ref<ExpenseCategoryRead[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)

  async function fetchAll() {
    loading.value = true
    error.value = null
    try {
      const data = await get<ExpenseCategoryListResponse>('/api/v1/expense-categories')
      items.value = data.items
    } catch (e: unknown) {
      error.value = e instanceof ApiError ? e.message : String(e)
    } finally {
      loading.value = false
    }
  }

  async function create(data: ExpenseCategoryWrite): Promise<ExpenseCategoryRead> {
    const result = await post<ExpenseCategoryRead>('/api/v1/expense-categories', data)
    await fetchAll()
    return result
  }

  async function update(id: string, data: ExpenseCategoryWrite): Promise<ExpenseCategoryRead> {
    const result = await put<ExpenseCategoryRead>(`/api/v1/expense-categories/${id}`, data)
    await fetchAll()
    return result
  }

  async function remove(id: string): Promise<void> {
    await del(`/api/v1/expense-categories/${id}`)
    await fetchAll()
  }

  return { items, loading, error, fetchAll, create, update, remove }
})

export const useUnitStore = defineStore('units', () => {
  const items = ref<UnitRead[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)

  async function fetchAll() {
    loading.value = true
    error.value = null
    try {
      const data = await get<UnitListResponse>('/api/v1/units')
      items.value = data.items
    } catch (e: unknown) {
      error.value = e instanceof ApiError ? e.message : String(e)
    } finally {
      loading.value = false
    }
  }

  async function create(data: UnitWrite): Promise<UnitRead> {
    const result = await post<UnitRead>('/api/v1/units', data)
    await fetchAll()
    return result
  }

  async function update(id: string, data: UnitWrite): Promise<UnitRead> {
    const result = await put<UnitRead>(`/api/v1/units/${id}`, data)
    await fetchAll()
    return result
  }

  async function remove(id: string): Promise<void> {
    await del(`/api/v1/units/${id}`)
    await fetchAll()
  }

  return { items, loading, error, fetchAll, create, update, remove }
})
