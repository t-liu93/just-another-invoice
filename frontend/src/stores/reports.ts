/**
 * Pinia store for M10 reporting (step 1: P/L report; step 2: BTW VAT return).
 *
 * Follows the same pattern as other stores in this project:
 * - Reactive state (loading, error, result)
 * - Async fetch function that populates state
 * - Exports types re-exported from schema.d.ts
 */
import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { components } from '../api/schema'
import { ApiError, get } from '../api/http'

type ProfitLossReport = components['schemas']['ProfitLossReport']
type ProfitLossSeriesItem = components['schemas']['ProfitLossSeriesItem']
type VatReturnReport = components['schemas']['VatReturnReport']

export type { ProfitLossReport, ProfitLossSeriesItem, VatReturnReport }

export const useReportsStore = defineStore('reports', () => {
  // P/L report state
  const plReport = ref<ProfitLossReport | null>(null)
  const plLoading = ref(false)
  const plError = ref<string | null>(null)

  // VAT return report state
  const vatReport = ref<VatReturnReport | null>(null)
  const vatLoading = ref(false)
  const vatError = ref<string | null>(null)

  /**
   * Fetch the P/L report for the given date range and granularity.
   *
   * @param from  ISO date string (YYYY-MM-DD), inclusive start.
   * @param to    ISO date string (YYYY-MM-DD), inclusive end.
   * @param granularity  'month' or 'quarter'.
   */
  async function fetchProfitLoss(
    from: string,
    to: string,
    granularity: 'month' | 'quarter' = 'month',
  ): Promise<void> {
    plLoading.value = true
    plError.value = null
    try {
      const params = new URLSearchParams({ from, to, granularity })
      plReport.value = await get<ProfitLossReport>(
        `/api/v1/reports/profit-loss?${params.toString()}`,
      )
    } catch (e: unknown) {
      plError.value = e instanceof ApiError ? e.message : String(e)
    } finally {
      plLoading.value = false
    }
  }

  /**
   * Fetch the BTW VAT return report for the given year and quarter.
   *
   * @param year     Calendar year (e.g. 2026).
   * @param quarter  Quarter number 1–4.
   */
  async function fetchVatReturn(year: number, quarter: number): Promise<void> {
    vatLoading.value = true
    vatError.value = null
    try {
      const params = new URLSearchParams({ year: String(year), quarter: String(quarter) })
      vatReport.value = await get<VatReturnReport>(
        `/api/v1/reports/vat-return?${params.toString()}`,
      )
    } catch (e: unknown) {
      vatError.value = e instanceof ApiError ? e.message : String(e)
    } finally {
      vatLoading.value = false
    }
  }

  return {
    plReport,
    plLoading,
    plError,
    fetchProfitLoss,
    vatReport,
    vatLoading,
    vatError,
    fetchVatReturn,
  }
})
