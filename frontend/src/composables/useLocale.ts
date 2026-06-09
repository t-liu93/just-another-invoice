/**
 * Locale composable – interface language with server-side persistence.
 *
 * Thin wrapper over the shared ``userPreferences`` module (single source of
 * truth for theme + locale).  Setting the locale updates vue-i18n + the
 * ``<html lang>`` attribute + the localStorage cache, and — once the account
 * preferences have loaded — persists to the server (PUT ``/settings/me``, full
 * theme + locale) so the language follows the account, mirroring ``useTheme``.
 *
 * Before login (``loaded`` is false) the change is cache-only; the server PUT
 * is skipped because the user is not authenticated yet.
 */
import { computed } from 'vue'

import {
  applyLocale,
  initLocaleFromCache,
  loaded,
  localePreference,
  persistUserPreferences,
  SUPPORTED_LOCALES,
  type LocalePreference,
} from './userPreferences'

export function useLocale() {
  const currentLocale = computed(() => localePreference.value)
  const availableLocales = SUPPORTED_LOCALES

  function setLocale(lang: LocalePreference): Promise<void> {
    applyLocale(lang)

    if (loaded.value) {
      return persistUserPreferences()
    }
    return Promise.resolve()
  }

  /** Restore the cached locale on boot (instant, before the server load). */
  function initLocale() {
    initLocaleFromCache()
  }

  return { currentLocale, availableLocales, setLocale, initLocale }
}
