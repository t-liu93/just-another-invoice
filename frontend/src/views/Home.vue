<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { useLocale } from '../composables/useLocale'

const { t } = useI18n()
const { currentLocale, availableLocales, setLocale, initLocale } = useLocale()

const backendStatus = ref<string | null>(null)

async function pingHealth() {
  try {
    const res = await fetch('/api/health')
    const data: { status: string; version?: string } = await res.json()
    const ver = data.version ? ` (v${data.version})` : ''
    backendStatus.value = `${data.status}${ver}`
  } catch {
    backendStatus.value = null
  }
}

onMounted(() => {
  initLocale()
  pingHealth()
})
</script>

<template>
  <n-space vertical align="center" justify="center" :size="24" style="min-height: 100vh">
    <n-h1 style="margin: 0">{{ t('app.title') }}</n-h1>
    <n-text depth="3">{{ t('app.subtitle') }}</n-text>

    <n-tag v-if="backendStatus" type="success" size="small">
      {{ t('health.backend', { status: backendStatus }) }}
    </n-tag>
    <n-tag v-else type="warning" size="small">{{ t('health.unreachable') }}</n-tag>

    <n-radio-group :value="currentLocale" @update:value="setLocale" size="small">
      <n-radio-button v-for="lang in availableLocales" :key="lang" :value="lang" :label="lang.toUpperCase()" />
    </n-radio-group>
  </n-space>
</template>
