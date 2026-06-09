/**
 * Shared vue-i18n instance.
 *
 * Exported as a module singleton (instead of being created inline in
 * ``main.ts``) so that non-setup code — e.g. the server-backed
 * ``composables/userPreferences`` loader invoked after login — can read and
 * write the active locale via ``i18n.global.locale``.
 */
import { createI18n } from 'vue-i18n'

import en from './locales/en.json'
import zh from './locales/zh.json'

export const i18n = createI18n({
  legacy: false,
  locale: 'en',
  fallbackLocale: 'en',
  messages: { en, zh },
})
