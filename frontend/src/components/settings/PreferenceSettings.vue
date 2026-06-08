<script setup lang="ts">
/**
 * Preference settings (no page chrome) – non-identity user/company preferences.
 *
 * M2.5 ships only the interface language here (session-scoped, same behaviour
 * as the previous header globe toggle).  Future defaults (date format, due
 * days, …) land in their own consuming milestones — a placeholder note marks
 * the slot.
 */
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

const { t, locale } = useI18n()

const languageOptions = [
  { label: 'English', value: 'en' },
  { label: '中文', value: 'zh' },
]

const currentLocale = computed({
  get: () => locale.value,
  set: (val: string) => {
    locale.value = val
  },
})
</script>

<template>
  <n-form label-placement="left" label-width="100">
    <n-form-item :label="t('settings.preferences.language')">
      <n-select v-model:value="currentLocale" :options="languageOptions" style="max-width: 200px" />
    </n-form-item>
    <n-text depth="3" style="font-size: 12px">
      {{ t('settings.preferences.languageHint') }}
    </n-text>
    <n-divider />
    <n-text depth="3" style="font-size: 12px">
      {{ t('settings.preferences.moreSoon') }}
    </n-text>
  </n-form>
</template>
