import { defineStore } from 'pinia'
import { ref } from 'vue'
import { ApiError, del, get, http, post, put } from '../api/http'
import type { components } from '../api/schema'

type MileageExpenseListItem = components['schemas']['MileageExpenseListItem']
type MileageExpenseListResponse = components['schemas']['MileageExpenseListResponse']
type MileageExpenseRead = components['schemas']['MileageExpenseRead']
type MileageExpenseWrite = components['schemas']['MileageExpenseWrite']
type MileageCalculationRead = components['schemas']['MileageCalculationRead']
type MileageCalculationRequest = components['schemas']['MileageCalculationRequest']

export type { MileageExpenseListItem, MileageExpenseRead, MileageExpenseWrite, MileageCalculationRead }

export const useMileageStore = defineStore('mileage', () => {
  const items = ref<MileageExpenseListItem[]>([])
  const total = ref(0)
  const loading = ref(false)
  const saving = ref(false)
  const error = ref<string | null>(null)
  const query = ref('')
  const transportTypeId = ref<string | null>(null)
  const dateFrom = ref<string | null>(null)
  const dateTo = ref<string | null>(null)
  const limit = ref(50)
  const offset = ref(0)
  const sortBy = ref<'trip_date' | 'created_at'>('trip_date')
  let listRequestSequence = 0
  let listAbortController: AbortController | null = null

  async function fetchMileage() {
    const requestSequence = ++listRequestSequence
    listAbortController?.abort()
    const controller = new AbortController()
    listAbortController = controller
    loading.value = true
    error.value = null
    try {
      const params = new URLSearchParams({ limit: String(limit.value), offset: String(offset.value), sort_by: sortBy.value })
      if (query.value) params.set('q', query.value)
      if (transportTypeId.value) params.set('transport_type_id', transportTypeId.value)
      if (dateFrom.value) params.set('date_from', dateFrom.value)
      if (dateTo.value) params.set('date_to', dateTo.value)
      const data = await http<MileageExpenseListResponse>(`/api/v1/mileage-expenses?${params.toString()}`, { signal: controller.signal })
      if (requestSequence !== listRequestSequence) return
      items.value = data.items
      total.value = data.total
    } catch (e: unknown) {
      if (requestSequence !== listRequestSequence || (e instanceof DOMException && e.name === 'AbortError')) return
      error.value = e instanceof ApiError ? e.message : String(e)
    } finally {
      if (requestSequence === listRequestSequence) loading.value = false
    }
  }

  function cancelMileageFetch() {
    ++listRequestSequence
    listAbortController?.abort()
    listAbortController = null
    loading.value = false
  }

  function clampOffsetForTotal(totalCount: number) {
    const lastOffset = totalCount <= 0 ? 0 : Math.floor((totalCount - 1) / limit.value) * limit.value
    offset.value = Math.min(offset.value, lastOffset)
  }

  function saveError(e: unknown) {
    error.value = e instanceof ApiError ? e.message : String(e)
  }

  async function calculate(body: MileageCalculationRequest, signal?: AbortSignal) {
    return await http<MileageCalculationRead>('/api/v1/mileage-expenses/calculate', { method: 'POST', body: JSON.stringify(body), signal })
  }

  async function getMileage(id: string) {
    return await get<MileageExpenseRead>(`/api/v1/mileage-expenses/${id}`)
  }

  async function createMileage(body: MileageExpenseWrite) {
    saving.value = true
    error.value = null
    try {
      return await post<MileageExpenseRead>('/api/v1/mileage-expenses', body)
    } catch (e: unknown) {
      saveError(e)
      throw e
    } finally { saving.value = false }
  }

  async function updateMileage(id: string, body: MileageExpenseWrite) {
    saving.value = true
    error.value = null
    try {
      return await put<MileageExpenseRead>(`/api/v1/mileage-expenses/${id}`, body)
    } catch (e: unknown) {
      saveError(e)
      throw e
    } finally { saving.value = false }
  }

  async function deleteMileage(id: string) {
    saving.value = true
    error.value = null
    try {
      await del(`/api/v1/mileage-expenses/${id}`)
    } catch (e: unknown) {
      saveError(e)
      throw e
    } finally { saving.value = false }
  }

  return { items, total, loading, saving, error, query, transportTypeId, dateFrom, dateTo, limit, offset, sortBy, fetchMileage, cancelMileageFetch, clampOffsetForTotal, calculate, getMileage, createMileage, updateMileage, deleteMileage }
})
