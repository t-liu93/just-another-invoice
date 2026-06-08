<script setup lang="ts">
/**
 * Shared application top bar.
 *
 * M2.5: a single gear opens the unified settings panel; a separate Company
 * icon goes to the business-identity page; plus a quick language toggle, the
 * current user, and logout.  No scattered person/theme entries anymore.
 */
import { useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import { useI18n } from 'vue-i18n'
import { useSettingsPanel } from '../composables/useSettingsPanel'
import { SettingsOutline, BusinessOutline, GlobeOutline, LogOutOutline } from '@vicons/ionicons5'
import { NIcon } from 'naive-ui'

const router = useRouter()
const auth = useAuthStore()
const { t, locale } = useI18n()
const { open } = useSettingsPanel()

async function handleLogout() {
  await auth.logout()
  router.push('/login')
}
</script>

<template>
  <n-layout-header bordered class="app-header">
    <div class="header-left">
      <h2 class="app-title" @click="router.push('/dashboard')">{{ t('app.title') }}</h2>
    </div>
    <div class="header-right">
      <n-button quaternary size="small" :title="t('settings.panel.open')" @click="open()">
        <template #icon>
          <n-icon><SettingsOutline /></n-icon>
        </template>
      </n-button>
      <n-button quaternary size="small" :title="t('settings.panel.company')" @click="router.push('/settings/company')">
        <template #icon>
          <n-icon><BusinessOutline /></n-icon>
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
</template>

<style scoped>
.app-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 24px;
}

.header-left .app-title {
  margin: 0;
  cursor: pointer;
  font-size: 18px;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 8px;
}
</style>
