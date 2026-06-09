<script setup lang="ts">
import { themeOverrides } from './styles/theme'
import { useTheme } from './composables/useTheme'
import { loadUserPreferences, resetUserPreferencesLoaded } from './composables/userPreferences'
import { registerPreferencesLoader } from './stores/auth'
import SettingsPanel from './components/settings/SettingsPanel.vue'

const { theme } = useTheme()

// Register the preferences load + reset hooks (theme + locale) so the auth
// store can pull a single GET /settings/me after login and suspend persistence
// on logout / auth-context change.
registerPreferencesLoader(loadUserPreferences, resetUserPreferencesLoaded)
</script>

<template>
  <n-config-provider :theme="theme" :theme-overrides="themeOverrides">
    <n-global-style />
    <n-message-provider>
      <n-dialog-provider>
        <router-view />
        <SettingsPanel />
      </n-dialog-provider>
    </n-message-provider>
  </n-config-provider>
</template>
