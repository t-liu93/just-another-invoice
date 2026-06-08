<script setup lang="ts">
import { themeOverrides } from './styles/theme'
import { useTheme } from './composables/useTheme'
import { registerThemeLoader } from './stores/auth'
import SettingsPanel from './components/settings/SettingsPanel.vue'

const { theme, loadFromServer } = useTheme()

// Register the theme loader so the auth store can trigger it after login.
registerThemeLoader(loadFromServer)
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
