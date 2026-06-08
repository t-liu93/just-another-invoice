<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../../stores/auth'
import { useI18n } from 'vue-i18n'
import { useTheme } from '../../composables/useTheme'
import { SunnyOutline, MoonOutline, GlobeOutline, LogOutOutline } from '@vicons/ionicons5'
import { NIcon } from 'naive-ui'

const router = useRouter()
const auth = useAuthStore()
const { t, locale } = useI18n()
const { preference, setPreference, toggle } = useTheme()

const isDark = computed(() => {
  const p = preference.value
  if (p === 'system') {
    return window.matchMedia('(prefers-color-scheme: dark)').matches
  }
  return p === 'dark'
})

const saving = ref(false)
const message = ref('')
const messageType = ref<'success' | 'error'>('success')

const themeOptions = computed(() => [
  { label: t('settings.preferences.themeSystem'), value: 'system' as const },
  { label: t('settings.preferences.themeLight'), value: 'light' as const },
  { label: t('settings.preferences.themeDark'), value: 'dark' as const },
])

const currentTheme = computed({
  get: () => preference.value,
  set: (val: 'system' | 'light' | 'dark') => {
    saving.value = true
    message.value = ''
    setPreference(val).then(() => {
      message.value = t('settings.preferences.saveSuccess')
      messageType.value = 'success'
      saving.value = false
    }).catch(() => {
      message.value = t('settings.preferences.saveFailed') || 'Save failed'
      messageType.value = 'error'
      saving.value = false
    })
  },
})

onMounted(() => {
  // Theme is already loaded from server by the auth flow.
})

async function handleLogout() {
  await auth.logout()
  router.push('/login')
}
</script>

<template>
  <div class="settings-page">
    <n-layout>
      <n-layout-header bordered class="app-header">
        <div class="header-left">
          <n-button quaternary size="small" @click="router.push('/dashboard')">
            ← {{ t('app.title') }}
          </n-button>
        </div>
        <div class="header-right">
          <n-button quaternary size="small" @click="router.push('/settings/company')">
            {{ t('settings.preferences.companyProfile') }}
          </n-button>
          <n-button quaternary size="small" @click="router.push('/settings/smtp')">
            SMTP
          </n-button>
          <n-button quaternary size="small" @click="toggle">
            <template #icon>
              <n-icon><SunnyOutline v-if="isDark" /><MoonOutline v-else /></n-icon>
            </template>
          </n-button>
          <n-button quaternary size="small" @click="locale = locale === 'en' ? 'zh' : 'en'">
            <template #icon>
              <n-icon><GlobeOutline /></n-icon>
            </template>
            {{ locale === 'en' ? '中文' : 'EN' }}
          </n-button>
          <n-tag v-if="auth.user" size="small" type="info">
            {{ auth.user.email }}
          </n-tag>
          <n-button quaternary size="small" type="error" @click="handleLogout">
            <template #icon>
              <n-icon><LogOutOutline /></n-icon>
            </template>
          </n-button>
        </div>
      </n-layout-header>

      <n-layout-content class="app-content">
        <div class="preferences-page">
          <n-card :title="t('settings.preferences.title')">
            <n-form label-placement="left" label-width="140">
              <n-form-item :label="t('settings.preferences.theme')">
                <n-radio-group v-model:value="currentTheme" :disabled="saving">
                  <n-space>
                    <n-radio
                      v-for="opt in themeOptions"
                      :key="opt.value"
                      :value="opt.value"
                      :label="opt.label"
                    />
                  </n-space>
                </n-radio-group>
              </n-form-item>
            </n-form>

            <n-alert v-if="message" :type="messageType" style="margin-top: 16px" closable @close="message = ''">
              {{ message }}
            </n-alert>
          </n-card>
        </div>
      </n-layout-content>
    </n-layout>
  </div>
</template>

<style scoped>
.app-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 24px;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 8px;
}

.app-content {
  min-height: calc(100vh - 57px);
}

.preferences-page {
  max-width: 640px;
  margin: 24px auto;
  padding: 0 24px;
}
</style>
