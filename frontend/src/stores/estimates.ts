import { defineStore } from 'pinia'
import { ref } from 'vue'
import { del, get, post, put } from '../api/http'
import { ApiError } from '../api/http'
import type { components } from '../api/schema'

type EstimateRead = components['schemas']['EstimateRead']
type EstimateWrite = components['schemas']['EstimateWrite']
type EstimateListItem = components['schemas']['EstimateListItem']
type EstimateListResponse = components['schemas']['EstimateListResponse']
type EstimateCalculationRequest = components['schemas']['EstimateCalculationRequest']
type EstimateCalculationRead = components['schemas']['EstimateCalculationRead']
type QuoteRead = components['schemas']['QuoteRead']

export type {
  EstimateRead,
  EstimateWrite,
  EstimateListItem,
  EstimateCalculationRequest,
  EstimateCalculationRead,
}

export const useEstimatesStore = defineStore('estimates', () => {
  const items = ref<EstimateListItem[]>([])
  const total = ref(0)
  const loading = ref(false)
  const saving = ref(false)
  const error = ref<string | null>(null)

  const query = ref('')
  const customerIdFilter = ref<string | null>(null)
  const limit = ref(50)
  const offset = ref(0)
  const sortBy = ref<'name' | 'created_at' | 'updated_at'>('updated_at')

  async function fetchEstimates() {
    loading.value = true
    error.value = null
    try {
      const params = new URLSearchParams()
      if (query.value) params.set('q', query.value)
      if (customerIdFilter.value) params.set('customer_id', customerIdFilter.value)
      params.set('limit', String(limit.value))
      params.set('offset', String(offset.value))
      params.set('sort_by', sortBy.value)
      const qs = params.toString()
      const url = `/api/v1/estimates${qs ? '?' + qs : ''}`
      const data = await get<EstimateListResponse>(url)
      items.value = data.items
      total.value = data.total
    } catch (e: unknown) {
      error.value = e instanceof ApiError ? e.message : String(e)
    } finally {
      loading.value = false
    }
  }

  async function fetchEstimate(id: string): Promise<EstimateRead> {
    return await get<EstimateRead>(`/api/v1/estimates/${id}`)
  }

  async function createEstimate(data: EstimateWrite): Promise<EstimateRead> {
    saving.value = true
    error.value = null
    try {
      return await post<EstimateRead>('/api/v1/estimates', data)
    } finally {
      saving.value = false
    }
  }

  async function updateEstimate(id: string, data: EstimateWrite): Promise<EstimateRead> {
    saving.value = true
    error.value = null
    try {
      return await put<EstimateRead>(`/api/v1/estimates/${id}`, data)
    } finally {
      saving.value = false
    }
  }

  async function deleteEstimate(id: string): Promise<void> {
    await del(`/api/v1/estimates/${id}`)
  }

  async function calculatePreview(data: EstimateCalculationRequest): Promise<EstimateCalculationRead> {
    return await post<EstimateCalculationRead>('/api/v1/estimates/calculate', data)
  }

  async function generateQuote(estimateId: string): Promise<QuoteRead> {
    return await post<QuoteRead>(`/api/v1/estimates/${estimateId}/generate-quote`, {})
  }

  return {
    items,
    total,
    loading,
    saving,
    error,
    query,
    customerIdFilter,
    limit,
    offset,
    sortBy,
    fetchEstimates,
    fetchEstimate,
    createEstimate,
    updateEstimate,
    deleteEstimate,
    calculatePreview,
    generateQuote,
  }
})
