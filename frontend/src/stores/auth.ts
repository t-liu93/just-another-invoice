/**
 * Auth store – manages current user state and authentication actions.
 *
 * Uses cookie-based session auth (credentials: 'include' in http.ts).
 *
 * Step 3 additions:
 * - `mfaSetup()`: call POST /auth/mfa/setup → returns {secret, otpauth_uri}.
 * - `mfaVerify(code)`: call POST /auth/mfa/verify → on success fetches user profile.
 * - `login()` no longer fetches the user (only pre-auth'd until MFA verify).
 */

import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { get, post } from '../api/http'
import type { components } from '../api/schema'

// Registered callbacks for server-backed user preferences (theme + locale).
// Kept as plain hooks so the store does not import UI composables at import
// time (avoids a circular dependency).
//  - ``_prefsLoader``: load the current account's preferences (one GET).
//  - ``_prefsReset``: suspend persistence on logout / auth-context change so a
//    stale snapshot can't be written to the next account before it loads.
let _prefsLoader: (() => Promise<void>) | null = null
let _prefsReset: (() => void) | null = null

/** Register the preferences load + reset hooks (called from App.vue). */
export function registerPreferencesLoader(loader: () => Promise<void>, reset?: () => void) {
  _prefsLoader = loader
  _prefsReset = reset ?? null
}

type UserRead = components['schemas']['UserRead']
type BootstrapResponse = components['schemas']['BootstrapResponse']
type LoginResponse = components['schemas']['LoginResponse']
type MfaSetupResponse = components['schemas']['MfaSetupResponse']

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
      // Load preferences (theme + locale) from the server when the user is
      // already authenticated (e.g. on page reload or after MFA verify).
      if (user.value && _prefsLoader) {
        // Fire-and-forget: don't block user loading on the preferences sync.
        void _prefsLoader()
      }
    } catch {
      user.value = null
      // Not authenticated: suspend preference persistence until a (re)load.
      _prefsReset?.()
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

  /**
   * Login with email and password.
   *
   * Sets a pre-auth cookie on success. The caller must inspect `next`
   * (`"mfa_setup"` or `"mfa_verify"`) and drive the appropriate MFA step.
   * The user profile is NOT fetched here — only after MFA verification.
   */
  async function login(email: string, password: string): Promise<string> {
    loading.value = true
    try {
      const result = await post<LoginResponse>('/api/v1/auth/login', { email, password })
      return result.next
    } finally {
      loading.value = false
    }
  }

  /**
   * MFA setup – generate a pending TOTP secret.
   *
   * Requires a pre-auth cookie (set by login).  Returns the secret and
   * otpauth URI so the frontend can render a QR code.
   */
  async function mfaSetup(): Promise<MfaSetupResponse> {
    loading.value = true
    try {
      return await post<MfaSetupResponse>('/api/v1/auth/mfa/setup', {})
    } finally {
      loading.value = false
    }
  }

  /**
   * MFA verify – validate a TOTP code and complete authentication.
   *
   * On success, clears the pre-auth cookie, sets the full session cookie,
   * and fetches the user profile.
   */
  async function mfaVerify(code: string): Promise<void> {
    loading.value = true
    try {
      await post('/api/v1/auth/mfa/verify', { code })
      await fetchUser()
      // Theme loader is already triggered by fetchUser() above.
      // No need to call it again here.
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
    // Suspend preference persistence so the next account that signs in within
    // this SPA session can't be written with the previous account's snapshot
    // before its own preferences load.
    _prefsReset?.()
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
    mfaSetup,
    mfaVerify,
    logout,
  }
})
