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
import { del, get, put } from '../api/http'
import { uploadFile } from '../api/http'
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

  /** Upload or replace the company logo. */
  async function uploadLogo(file: File): Promise<void> {
    error.value = null
    try {
      const result = await uploadFile<{
        logo_url: string
        mime_type: string
        byte_size: number
      }>('/api/v1/company/logo', file)
      // Refresh company to update has_logo / logo_url.
      if (company.value) {
        company.value = { ...company.value, has_logo: true, logo_url: result.logo_url }
      }
    } catch (e: unknown) {
      if (e instanceof ApiError) {
        error.value = e.message
      } else {
        error.value = String(e)
      }
      throw e
    }
  }

  /** Delete the company logo. */
  async function deleteLogo(): Promise<void> {
    error.value = null
    try {
      await del('/api/v1/company/logo')
      if (company.value) {
        company.value = { ...company.value, has_logo: false, logo_url: null }
      }
    } catch (e: unknown) {
      if (e instanceof ApiError) {
        error.value = e.message
      } else {
        error.value = String(e)
      }
      throw e
    }
  }

  return {
    company,
    loading,
    saving,
    error,
    hasCompany,
    fetchCompany,
    saveCompany,
    uploadLogo,
    deleteLogo,
  }
})
