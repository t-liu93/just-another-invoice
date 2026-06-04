<script setup lang="ts">
import { themeOverrides } from './styles/theme'
import { useTheme } from './composables/useTheme'

const { theme } = useTheme()
</script>

<template>
  <n-config-provider :theme="theme" :theme-overrides="themeOverrides">
    <n-global-style />
    <n-message-provider>
      <n-dialog-provider>
        <router-view />
      </n-dialog-provider>
    </n-message-provider>
  </n-config-provider>
</template>
