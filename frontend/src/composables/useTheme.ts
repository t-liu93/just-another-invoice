/**
 * Dark mode composable – Naive UI theme switching with localStorage persistence.
 *
 * Default follows the system preference (prefers-color-scheme).
 * Persisted to localStorage under key 'jai-theme'.
 */

import { ref, watch } from 'vue'
import { darkTheme } from 'naive-ui'
import type { GlobalTheme } from 'naive-ui'

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

const preference = ref<ThemePreference>(getInitialPreference())

export function useTheme() {
  function resolveTheme(): GlobalTheme | null {
    const resolved = preference.value === 'system' ? getSystemPreference() : preference.value
    return resolved === 'dark' ? darkTheme : null
  }

  const theme = ref<GlobalTheme | null>(resolveTheme())

  function setPreference(p: ThemePreference) {
    preference.value = p
    localStorage.setItem(STORAGE_KEY, p)
  }

  function toggle() {
    if (preference.value === 'system') {
      setPreference(getSystemPreference() === 'dark' ? 'light' : 'dark')
    } else {
      setPreference(preference.value === 'dark' ? 'light' : 'dark')
    }
  }

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

  return {
    theme,
    preference,
    setPreference,
    toggle,
  }
}
