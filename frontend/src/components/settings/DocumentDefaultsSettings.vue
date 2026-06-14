<script setup lang="ts">
/**
 * Document default locale settings — company-level default PDF/email language.
 *
 * Loads from / saves to GET/PUT /settings/document-defaults.
 * This is the third tier of the D2 locale resolution chain:
 *   export override → customer.locale → *this* → "en".
 */
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { get, put } from '../../api/http'
import type { components } from '../../api/schema'

type DocumentDefaultsRead = components['schemas']['DocumentDefaultsRead']

const { t } = useI18n()

type LocaleValue = 'en' | 'zh'

const locale = ref<LocaleValue>('en')
const loading = ref(false)
const saving = ref(false)
const message = ref('')
const messageType = ref<'success' | 'error'>('success')

const localeOptions = [
  { label: 'English', value: 'en' },
  { label: '中文', value: 'zh' },
]

onMounted(async () => {
  loading.value = true
  try {
    const data = await get<DocumentDefaultsRead>('/api/v1/settings/document-defaults')
    locale.value = (data.locale as LocaleValue | undefined) ?? 'en'
  } catch {
    // If company not set up yet, keep default
  } finally {
    loading.value = false
  }
})

async function handleSave() {
  saving.value = true
  message.value = ''
  try {
    const data = await put<DocumentDefaultsRead>('/api/v1/settings/document-defaults', {
      locale: locale.value,
    })
    locale.value = (data.locale as LocaleValue | undefined) ?? 'en'
    message.value = t('settings.documentDefaults.saveSuccess')
    messageType.value = 'success'
  } catch (e: unknown) {
    message.value = e instanceof Error ? e.message : t('settings.documentDefaults.saveFailed')
    messageType.value = 'error'
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <n-spin :show="loading">
    <n-form label-placement="left" label-width="160">
      <n-h4 style="margin: 0 0 16px">{{ t('settings.documentDefaults.title') }}</n-h4>

      <n-form-item :label="t('settings.documentDefaults.locale')">
        <n-select
          v-model:value="locale"
          :options="localeOptions"
          style="max-width: 200px"
        />
      </n-form-item>
      <n-text depth="3" style="font-size: 12px">
        {{ t('settings.documentDefaults.localeHint') }}
      </n-text>

      <n-alert
        v-if="message"
        :type="messageType"
        closable
        style="margin-top: 12px"
        @close="message = ''"
      >
        {{ message }}
      </n-alert>

      <div style="margin-top: 16px">
        <n-button type="primary" :loading="saving" @click="handleSave">
          {{ t('settings.documentDefaults.save') }}
        </n-button>
      </div>
    </n-form>
  </n-spin>
</template>
