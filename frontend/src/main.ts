import { createApp } from 'vue'
import { createPinia } from 'pinia'

import 'vfonts/Lato.css'
import 'vfonts/FiraCode.css'
import './style.css'

import App from './App.vue'
import router from './router'
import { i18n } from './i18n'
import { initLocaleFromCache } from './composables/userPreferences'

// Restore the cached UI language immediately, before the app mounts (the
// server-backed value, if any, overrides it after login).
initLocaleFromCache()

const app = createApp(App)
app.use(createPinia())
app.use(i18n)
app.use(router)

app.mount('#app')
