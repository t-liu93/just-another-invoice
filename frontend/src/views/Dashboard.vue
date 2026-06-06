<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import { useI18n } from 'vue-i18n'
import { useTheme } from '../composables/useTheme'
import { get } from '../api/http'
import type { components } from '../api/schema'
import { SunnyOutline, MoonOutline, GlobeOutline, SettingsOutline, BusinessOutline, LogOutOutline } from '@vicons/ionicons5'
import { NIcon } from 'naive-ui'

type HealthResponse = components['schemas']['HealthResponse']

const router = useRouter()
const auth = useAuthStore()
const { t, locale } = useI18n()
const { toggle, preference } = useTheme()

const healthStatus = ref<string>('')

const isDark = computed(() => {
  const p = preference.value
  if (p === 'system') {
    return window.matchMedia('(prefers-color-scheme: dark)').matches
  }
  return p === 'dark'
})

onMounted(async () => {
  try {
    const health = await get<HealthResponse>('/api/health')
    healthStatus.value = health.status
  } catch {
    healthStatus.value = 'error'
  }
})

async function handleLogout() {
  await auth.logout()
  router.push('/login')
}
</script>

<template>
  <div class="dashboard">
    <n-layout>
      <n-layout-header bordered class="app-header">
        <div class="header-left">
          <h2>{{ t('app.title') }}</h2>
        </div>
        <div class="header-right">
          <n-button quaternary size="small" @click="router.push('/settings/company')">
            <template #icon>
              <n-icon><BusinessOutline /></n-icon>
            </template>
          </n-button>
          <n-button quaternary size="small" @click="router.push('/settings/smtp')">
            <template #icon>
              <n-icon><SettingsOutline /></n-icon>
            </template>
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
        <n-space vertical align="center" justify="center" :size="24" style="padding-top: 80px">
          <h1>{{ t('dashboard.welcome') }}</h1>
          <n-tag :type="healthStatus === 'ok' ? 'success' : 'error'">
            {{ t('health.status') }}: {{ healthStatus }}
          </n-tag>
          <p class="dashboard-hint">{{ t('dashboard.emptyHint') }}</p>
        </n-space>
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

.header-left h2 {
  margin: 0;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 8px;
}

.app-content {
  min-height: calc(100vh - 57px);
}

.dashboard-hint {
  opacity: 0.5;
  font-size: 14px;
}
</style>
