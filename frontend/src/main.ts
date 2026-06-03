import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { createI18n } from 'vue-i18n'

import 'vfonts/Lato.css'
import 'vfonts/FiraCode.css'
import './style.css'

import en from './locales/en.json'
import zh from './locales/zh.json'

import App from './App.vue'
import router from './router'

const i18n = createI18n({
  legacy: false,
  locale: 'en',
  fallbackLocale: 'en',
  messages: { en, zh },
})

const app = createApp(App)
app.use(createPinia())
app.use(i18n)
app.use(router)

app.mount('#app')
