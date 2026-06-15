/**
 * Pinia store for M10 reporting (step 1: P/L report).
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

export type { ProfitLossReport, ProfitLossSeriesItem }

export const useReportsStore = defineStore('reports', () => {
  // P/L report state
  const plReport = ref<ProfitLossReport | null>(null)
  const plLoading = ref(false)
  const plError = ref<string | null>(null)

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

  return {
    plReport,
    plLoading,
    plError,
    fetchProfitLoss,
  }
})
