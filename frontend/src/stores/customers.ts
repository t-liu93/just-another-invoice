/**
 * Customers store – manages customer list, CRUD, search and pagination.
 *
 * Uses Composition API pattern (same as company store).
 */

import { defineStore } from 'pinia'
import { ref } from 'vue'
import { del, get, post, put } from '../api/http'
import { ApiError } from '../api/http'
import type { components } from '../api/schema'

type CustomerRead = components['schemas']['CustomerRead']
type CustomerWrite = components['schemas']['CustomerWrite']
type CustomerListResponse = components['schemas']['CustomerListResponse']

export const useCustomersStore = defineStore('customers', () => {
  const items = ref<CustomerRead[]>([])
  const total = ref(0)
  const loading = ref(false)
  const saving = ref(false)
  const error = ref<string | null>(null)

  // Search / pagination state.
  const query = ref('')
  const limit = ref(50)
  const offset = ref(0)

  /** Fetch the customer list with current search/pagination params. */
  async function fetchCustomers() {
    loading.value = true
    error.value = null
    try {
      const params = new URLSearchParams()
      if (query.value) params.set('q', query.value)
      params.set('limit', String(limit.value))
      params.set('offset', String(offset.value))
      const qs = params.toString()
      const url = `/api/v1/customers${qs ? '?' + qs : ''}`
      const data = await get<CustomerListResponse>(url)
      items.value = data.items
      total.value = data.total
    } catch (e: unknown) {
      if (e instanceof ApiError) {
        error.value = e.message
      } else {
        error.value = String(e)
      }
    } finally {
      loading.value = false
    }
  }

  /** Fetch a single customer by id. */
  async function fetchCustomer(id: string): Promise<CustomerRead> {
    return await get<CustomerRead>(`/api/v1/customers/${id}`)
  }

  /** Create a new customer. */
  async function createCustomer(data: CustomerWrite): Promise<CustomerRead> {
    saving.value = true
    error.value = null
    try {
      const result = await post<CustomerRead>('/api/v1/customers', data)
      return result
    } finally {
      saving.value = false
    }
  }

  /** Update an existing customer. */
  async function updateCustomer(
    id: string,
    data: CustomerWrite,
  ): Promise<CustomerRead> {
    saving.value = true
    error.value = null
    try {
      const result = await put<CustomerRead>(`/api/v1/customers/${id}`, data)
      return result
    } finally {
      saving.value = false
    }
  }

  /** Delete a customer. */
  async function deleteCustomer(id: string): Promise<void> {
    await del(`/api/v1/customers/${id}`)
  }

  return {
    items,
    total,
    loading,
    saving,
    error,
    query,
    limit,
    offset,
    fetchCustomers,
    fetchCustomer,
    createCustomer,
    updateCustomer,
    deleteCustomer,
  }
})
