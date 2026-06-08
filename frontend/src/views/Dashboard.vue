<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { get } from '../api/http'
import type { components } from '../api/schema'
import AppHeader from '../components/AppHeader.vue'

type HealthResponse = components['schemas']['HealthResponse']

const { t } = useI18n()

const healthStatus = ref<string>('')

onMounted(async () => {
  try {
    const health = await get<HealthResponse>('/api/health')
    healthStatus.value = health.status
  } catch {
    healthStatus.value = 'error'
  }
})
</script>

<template>
  <div class="dashboard">
    <n-layout>
      <AppHeader />

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
.app-content {
  min-height: calc(100vh - 57px);
}

.dashboard-hint {
  opacity: 0.5;
  font-size: 14px;
}
</style>
