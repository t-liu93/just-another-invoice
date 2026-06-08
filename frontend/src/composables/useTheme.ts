/**
 * Dark mode composable – Naive UI theme switching with server-side persistence.
 *
 * Default follows the system preference (prefers-color-scheme).
 * After login the theme is loaded from the user's account (GET /settings/me).
 * Changes are persisted to both localStorage (fast cache) and the server
 * (PUT /settings/me) so the preference follows the account.
 *
 * M2 Step 3: localStorage becomes a cache layer; the server is the source of
 * truth for authenticated users.  On first load before API response, the
 * localStorage value is used as a fallback for instant UI response.
 *
 * IMPORTANT: ``preference``, ``theme``, ``loaded``, and ``resolveTheme`` are
 * module-level singletons so that every component calling ``useTheme()``
 * shares the same state.  Previous bug: ``loaded`` was per-call, causing
 * ``setPreference`` to skip ``persistToServer`` in components other than
 * the one that called ``loadFromServer``.
 */

import { ref, watch } from 'vue'
import { darkTheme } from 'naive-ui'
import type { GlobalTheme } from 'naive-ui'
import { get, put } from '../api/http'

const STORAGE_KEY = 'jai-theme'

type ThemePreference = 'light' | 'dark' | 'system'

function getSystemPreference(): 'light' | 'dark' {
  if (window.matchMedia('(prefers-color-scheme: dark)').matches) {
    return 'dark'
  }
  return 'light'
}

function getInitialPreference(): ThemePreference {
  const stored = localStorage.getItem(STORAGE_KEY)
  if (stored === 'light' || stored === 'dark' || stored === 'system') {
    return stored
  }
  return 'system'
}

// Module-level shared state — all useTheme() callers see the same refs.
const preference = ref<ThemePreference>(getInitialPreference())
const loaded = ref(false)

function resolveTheme(): GlobalTheme | null {
  const resolved = preference.value === 'system' ? getSystemPreference() : preference.value
  return resolved === 'dark' ? darkTheme : null
}

const theme = ref<GlobalTheme | null>(resolveTheme())

// Sync theme ref whenever preference changes.
watch(preference, () => {
  theme.value = resolveTheme()
})

// Listen for system theme changes when in 'system' mode.
if (typeof window !== 'undefined') {
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
    if (preference.value === 'system') {
      theme.value = resolveTheme()
    }
  })
}

export function useTheme() {
  function setPreference(p: ThemePreference, syncToServer = true): Promise<void> {
    preference.value = p
    localStorage.setItem(STORAGE_KEY, p)

    if (syncToServer && loaded.value) {
      return persistToServer(p)
    }
    return Promise.resolve()
  }

  function toggle() {
    if (preference.value === 'system') {
      void setPreference(getSystemPreference() === 'dark' ? 'light' : 'dark')
    } else {
      void setPreference(preference.value === 'dark' ? 'light' : 'dark')
    }
  }

  /** Load theme preference from the server (call after login). */
  async function loadFromServer() {
    try {
      const data = await get<{ theme: ThemePreference }>('/api/v1/settings/me')
      if (data && data.theme) {
        preference.value = data.theme
        localStorage.setItem(STORAGE_KEY, data.theme)
        // theme ref is updated by the watch on preference.
      }
    } catch {
      // Non-critical: keep localStorage value.
    }
    loaded.value = true
  }

  /** Persist theme preference to the server. */
  async function persistToServer(p: ThemePreference) {
    await put('/api/v1/settings/me', { theme: p })
  }

  return {
    theme,
    preference,
    loaded,
    setPreference,
    toggle,
    loadFromServer,
  }
}
