/**
 * Dark mode composable – Naive UI theme switching with server-side persistence.
 *
 * The theme preference itself lives in the shared ``userPreferences`` module
 * (single source of truth for theme + locale, both served by ``/settings/me``).
 * This composable adds the Naive UI ``GlobalTheme`` resolution on top:
 *
 * - Default follows the system preference (prefers-color-scheme).
 * - After login the value is loaded from the account (``loadUserPreferences``).
 * - Changes are persisted to localStorage (fast cache) and the server (PUT
 *   ``/settings/me``, full theme + locale) so the preference follows the
 *   account.  ``localStorage`` is a cache; the server is the source of truth.
 *
 * State (``themePreference``, ``loaded``) is module-level shared so every
 * caller — and ``useLocale`` — sees the same values.
 */

import { ref, watch } from 'vue'
import { darkTheme } from 'naive-ui'
import type { GlobalTheme } from 'naive-ui'

import {
  applyThemePreference,
  loaded,
  persistUserPreferences,
  themePreference,
  type ThemePreference,
} from './userPreferences'

function getSystemPreference(): 'light' | 'dark' {
  if (window.matchMedia('(prefers-color-scheme: dark)').matches) {
    return 'dark'
  }
  return 'light'
}

function resolveTheme(): GlobalTheme | null {
  const resolved =
    themePreference.value === 'system' ? getSystemPreference() : themePreference.value
  return resolved === 'dark' ? darkTheme : null
}

const theme = ref<GlobalTheme | null>(resolveTheme())

// Sync the Naive UI theme ref whenever the shared preference changes.
watch(themePreference, () => {
  theme.value = resolveTheme()
})

// Listen for system theme changes when in 'system' mode.
if (typeof window !== 'undefined') {
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
    if (themePreference.value === 'system') {
      theme.value = resolveTheme()
    }
  })
}

export function useTheme() {
  function setPreference(p: ThemePreference, syncToServer = true): Promise<void> {
    applyThemePreference(p)

    if (syncToServer && loaded.value) {
      return persistUserPreferences()
    }
    return Promise.resolve()
  }

  function toggle() {
    if (themePreference.value === 'system') {
      void setPreference(getSystemPreference() === 'dark' ? 'light' : 'dark')
    } else {
      void setPreference(themePreference.value === 'dark' ? 'light' : 'dark')
    }
  }

  return {
    theme,
    preference: themePreference,
    loaded,
    setPreference,
    toggle,
  }
}
