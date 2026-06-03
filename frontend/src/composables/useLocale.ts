import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import type { Locale } from 'vue-i18n'

const SUPPORTED_LOCALES: Locale[] = ['en', 'zh']
const STORAGE_KEY = 'jai-locale'

export function useLocale() {
  const { locale } = useI18n()

  const currentLocale = computed(() => locale.value)
  const availableLocales = SUPPORTED_LOCALES

  function setLocale(lang: Locale) {
    locale.value = lang
    localStorage.setItem(STORAGE_KEY, lang)
    document.documentElement.lang = lang
  }

  /** Restore persisted locale on boot. */
  function initLocale() {
    const saved = localStorage.getItem(STORAGE_KEY)
    if (saved && SUPPORTED_LOCALES.includes(saved)) {
      locale.value = saved
      document.documentElement.lang = saved
    }
  }

  return { currentLocale, availableLocales, setLocale, initLocale }
}
