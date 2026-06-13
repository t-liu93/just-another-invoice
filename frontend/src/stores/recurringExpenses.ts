import { defineStore } from 'pinia'
import { ref } from 'vue'
import { del, get, post, put, ApiError } from '../api/http'
import type { components } from '../api/schema'

type RecurringExpenseRead = components['schemas']['RecurringExpenseRead']
type RecurringExpenseInput = components['schemas']['RecurringExpenseInput']
type RecurringExpenseListResponse = components['schemas']['RecurringExpenseListResponse']
type RunNowResponse = components['schemas']['RunNowResponse']

export type { RecurringExpenseRead, RecurringExpenseInput, RunNowResponse }

export const useRecurringExpensesStore = defineStore('recurringExpenses', () => {
  const items = ref<RecurringExpenseRead[]>([])
  const total = ref(0)
  const loading = ref(false)
  const saving = ref(false)
  const error = ref<string | null>(null)

  const activeFilter = ref<boolean | null>(null)
  const limit = ref(50)
  const offset = ref(0)

  async function fetchRecurringExpenses() {
    loading.value = true
    error.value = null
    try {
      const params = new URLSearchParams()
      if (activeFilter.value !== null) params.set('active', String(activeFilter.value))
      params.set('limit', String(limit.value))
      params.set('offset', String(offset.value))
      const qs = params.toString()
      const url = `/api/v1/recurring-expenses${qs ? '?' + qs : ''}`
      const data = await get<RecurringExpenseListResponse>(url)
      items.value = data.items
      total.value = data.total
    } catch (e: unknown) {
      error.value = e instanceof ApiError ? e.message : String(e)
    } finally {
      loading.value = false
    }
  }

  async function getRecurringExpense(id: string): Promise<RecurringExpenseRead> {
    return await get<RecurringExpenseRead>(`/api/v1/recurring-expenses/${id}`)
  }

  async function createRecurringExpense(body: RecurringExpenseInput): Promise<RecurringExpenseRead> {
    saving.value = true
    error.value = null
    try {
      return await post<RecurringExpenseRead>('/api/v1/recurring-expenses', body)
    } catch (e: unknown) {
      if (e instanceof ApiError) error.value = e.message
      else error.value = String(e)
      throw e
    } finally {
      saving.value = false
    }
  }

  async function updateRecurringExpense(id: string, body: RecurringExpenseInput): Promise<RecurringExpenseRead> {
    saving.value = true
    error.value = null
    try {
      return await put<RecurringExpenseRead>(`/api/v1/recurring-expenses/${id}`, body)
    } catch (e: unknown) {
      if (e instanceof ApiError) error.value = e.message
      else error.value = String(e)
      throw e
    } finally {
      saving.value = false
    }
  }

  async function deleteRecurringExpense(id: string): Promise<void> {
    saving.value = true
    error.value = null
    try {
      await del<null>(`/api/v1/recurring-expenses/${id}`)
    } catch (e: unknown) {
      if (e instanceof ApiError) error.value = e.message
      else error.value = String(e)
      throw e
    } finally {
      saving.value = false
    }
  }

  async function runNow(id: string): Promise<RunNowResponse> {
    saving.value = true
    error.value = null
    try {
      return await post<RunNowResponse>(`/api/v1/recurring-expenses/${id}/run-now`, {})
    } catch (e: unknown) {
      if (e instanceof ApiError) error.value = e.message
      else error.value = String(e)
      throw e
    } finally {
      saving.value = false
    }
  }

  return {
    items,
    total,
    loading,
    saving,
    error,
    activeFilter,
    limit,
    offset,
    fetchRecurringExpenses,
    getRecurringExpense,
    createRecurringExpense,
    updateRecurringExpense,
    deleteRecurringExpense,
    runNow,
  }
})
