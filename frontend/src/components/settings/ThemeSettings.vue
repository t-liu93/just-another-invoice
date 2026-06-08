<script setup lang="ts">
/**
 * Theme settings (no page chrome) – radio selector backed by useTheme.
 *
 * The preference is persisted to the account (USER-level setting) by
 * useTheme().setPreference, with localStorage as a fast cache.
 */
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useTheme } from '../../composables/useTheme'

const { t } = useI18n()
const { preference, setPreference } = useTheme()

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
    setPreference(val)
      .then(() => {
        message.value = t('settings.preferences.saveSuccess')
        messageType.value = 'success'
        saving.value = false
      })
      .catch(() => {
        message.value = t('settings.preferences.saveFailed')
        messageType.value = 'error'
        saving.value = false
      })
  },
})
</script>

<template>
  <n-form label-placement="left" label-width="100">
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

    <n-alert v-if="message" :type="messageType" closable @close="message = ''">
      {{ message }}
    </n-alert>
  </n-form>
</template>
