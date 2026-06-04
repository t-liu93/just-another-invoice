/**
 * Auth store – manages current user state and authentication actions.
 *
 * Uses cookie-based session auth (credentials: 'include' in http.ts).
 */

import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { get, post } from '../api/http'
import type { components } from '../api/schema'

type UserRead = components['schemas']['UserRead']
type BootstrapResponse = components['schemas']['BootstrapResponse']
type LoginResponse = components['schemas']['LoginResponse']

export const useAuthStore = defineStore('auth', () => {
  const user = ref<UserRead | null>(null)
  const bootstrap = ref<BootstrapResponse | null>(null)
  const loading = ref(false)
  const initialised = ref(false)

  /** Fetch the bootstrap state (registration open, onboarding completed). */
  async function fetchBootstrap() {
    bootstrap.value = await get<BootstrapResponse>('/api/v1/auth/bootstrap')
  }

  /** Fetch the current authenticated user (or null if not logged in). */
  async function fetchUser() {
    try {
      user.value = await get<UserRead>('/api/v1/users/me')
    } catch {
      user.value = null
    }
  }

  /** Initialise the store – call once from the router guard. */
  async function initialise() {
    if (initialised.value) return
    try {
      await fetchBootstrap()
      await fetchUser()
    } catch {
      // Best-effort; the guard will decide routing based on available data.
    } finally {
      initialised.value = true
    }
  }

  /**
   * Register a new user. Only succeeds when registration is open.
   *
   * Registration does NOT authenticate the user (the endpoint sets no session
   * cookie), so we must not populate `user`. We refresh bootstrap instead so
   * the router guard sees registration as now-closed and routes to /login
   * rather than bouncing back to /register.
   */
  async function register(email: string, password: string) {
    loading.value = true
    try {
      await post<UserRead>('/api/v1/auth/register', { email, password })
      await fetchBootstrap()
    } finally {
      loading.value = false
    }
  }

  /** Login with email and password. Sets session cookie on success. */
  async function login(email: string, password: string): Promise<string> {
    loading.value = true
    try {
      const result = await post<LoginResponse>('/api/v1/auth/login', { email, password })
      // After successful login, fetch the full user profile.
      await fetchUser()
      return result.next
    } finally {
      loading.value = false
    }
  }

  /** Logout and clear the session cookie. */
  async function logout() {
    try {
      await post('/api/v1/auth/logout', {})
    } catch {
      // Ignore – cookie may already be cleared.
    }
    user.value = null
  }

  /** Whether the user is currently authenticated. */
  const isAuthenticated = computed(() => user.value !== null)

  return {
    user,
    bootstrap,
    loading,
    initialised,
    isAuthenticated,
    fetchBootstrap,
    fetchUser,
    initialise,
    register,
    login,
    logout,
  }
})
