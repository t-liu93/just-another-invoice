/**
 * VAT store – manages vat_rate and vat_treatment dictionaries (M4 step 1).
 */

import { defineStore } from 'pinia'
import { ref } from 'vue'
import { get, post, put, del } from '../api/http'
import { ApiError } from '../api/http'
import type { components } from '../api/schema'

type VatRateRead = components['schemas']['VatRateRead']
type VatRateWrite = components['schemas']['VatRateWrite']
type VatRateListResponse = components['schemas']['VatRateListResponse']
type VatTreatmentRead = components['schemas']['VatTreatmentRead']
type VatTreatmentWrite = components['schemas']['VatTreatmentWrite']
type VatTreatmentListResponse = components['schemas']['VatTreatmentListResponse']
type VatTreatmentSide = components['schemas']['VatTreatmentSide']

export const useVatStore = defineStore('vat', () => {
  const rates = ref<VatRateRead[]>([])
  const treatments = ref<VatTreatmentRead[]>([])
  const loadingRates = ref(false)
  const loadingTreatments = ref(false)
  const error = ref<string | null>(null)

  async function fetchRates() {
    loadingRates.value = true
    error.value = null
    try {
      const data = await get<VatRateListResponse>('/api/v1/vat-rates')
      rates.value = data.items
    } catch (e: unknown) {
      error.value = e instanceof ApiError ? e.message : String(e)
    } finally {
      loadingRates.value = false
    }
  }

  async function fetchTreatments(side?: VatTreatmentSide) {
    loadingTreatments.value = true
    error.value = null
    try {
      const url = side ? `/api/v1/vat-treatments?side=${side}` : '/api/v1/vat-treatments'
      const data = await get<VatTreatmentListResponse>(url)
      treatments.value = data.items
    } catch (e: unknown) {
      error.value = e instanceof ApiError ? e.message : String(e)
    } finally {
      loadingTreatments.value = false
    }
  }

  async function createRate(data: VatRateWrite): Promise<VatRateRead> {
    const result = await post<VatRateRead>('/api/v1/vat-rates', data)
    await fetchRates()
    return result
  }

  async function updateRate(id: string, data: VatRateWrite): Promise<VatRateRead> {
    const result = await put<VatRateRead>(`/api/v1/vat-rates/${id}`, data)
    await fetchRates()
    return result
  }

  async function deleteRate(id: string): Promise<void> {
    await del(`/api/v1/vat-rates/${id}`)
    await fetchRates()
  }

  async function createTreatment(data: VatTreatmentWrite): Promise<VatTreatmentRead> {
    const result = await post<VatTreatmentRead>('/api/v1/vat-treatments', data)
    await fetchTreatments()
    return result
  }

  async function updateTreatment(id: string, data: VatTreatmentWrite): Promise<VatTreatmentRead> {
    const result = await put<VatTreatmentRead>(`/api/v1/vat-treatments/${id}`, data)
    await fetchTreatments()
    return result
  }

  async function deleteTreatment(id: string): Promise<void> {
    await del(`/api/v1/vat-treatments/${id}`)
    await fetchTreatments()
  }

  return {
    rates,
    treatments,
    loadingRates,
    loadingTreatments,
    error,
    fetchRates,
    fetchTreatments,
    createRate,
    updateRate,
    deleteRate,
    createTreatment,
    updateTreatment,
    deleteTreatment,
  }
})
