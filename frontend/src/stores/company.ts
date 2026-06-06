/**
 * Company store – manages the singleton company business profile.
 *
 * The company profile is fetched once after authentication and can be
 * updated via PUT /api/v1/company.
 *
 * Error handling:
 * - 204 (no company yet) → company = null, no error.
 * - Real errors (500, 403, network) → error ref set, company left unchanged
 *   so stale data is not silently replaced by null.
 */

import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { get, put } from '../api/http'
import { ApiError } from '../api/http'
import type { components } from '../api/schema'

type CompanyRead = components['schemas']['CompanyRead']
type CompanyWrite = components['schemas']['CompanyWrite']

export const useCompanyStore = defineStore('company', () => {
  const company = ref<CompanyRead | null>(null)
  const loading = ref(false)
  const saving = ref(false)
  const error = ref<string | null>(null)

  /** Fetch the company profile from the backend. */
  async function fetchCompany() {
    loading.value = true
    error.value = null
    try {
      const data = await get<CompanyRead>('/api/v1/company')
      // http wrapper returns null for 204 (no company yet).
      company.value = data
    } catch (e: unknown) {
      // Real error (500, 403, network) — do NOT silently set company=null.
      if (e instanceof ApiError) {
        error.value = e.message
      } else {
        error.value = String(e)
      }
    } finally {
      loading.value = false
    }
  }

  /** Create or update the company profile. */
  async function saveCompany(data: CompanyWrite): Promise<CompanyRead> {
    saving.value = true
    error.value = null
    try {
      const result = await put<CompanyRead>('/api/v1/company', data)
      company.value = result
      return result
    } finally {
      saving.value = false
    }
  }

  /** Whether the company profile has been created. */
  const hasCompany = computed(() => company.value !== null)

  return {
    company,
    loading,
    saving,
    error,
    hasCompany,
    fetchCompany,
    saveCompany,
  }
})
