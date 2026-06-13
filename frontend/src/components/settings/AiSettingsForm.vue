<script setup lang="ts">
/**
 * AI settings form — mirrors SmtpSettingsForm.vue.
 *
 * Loads from / saves to GET/PUT /settings/ai.
 * Tests connectivity via POST /settings/ai/test.
 *
 * API-key safety rules:
 * - On load: backend returns api_key_set (bool), never the raw key.
 * - On save: null = keep existing, "" = clear, non-empty = update.
 * - The raw key is never displayed back to the user.
 */
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { get, put, post } from '../../api/http'
import type { components } from '../../api/schema'

type AiSettingsRead = components['schemas']['AiSettingsRead']
type AiTestResult = components['schemas']['AiTestResult']

const { t } = useI18n()

const enabled = ref(false)
const baseUrl = ref('https://api.openai.com/v1')
const model = ref('')
const apiKey = ref('')
const apiKeySet = ref(false)
const clearApiKey = ref(false)
const receiptPrompt = ref('')

const loading = ref(false)
const saving = ref(false)
const testing = ref(false)
const message = ref('')
const messageType = ref<'success' | 'error' | 'info'>('success')
const testResult = ref<AiTestResult | null>(null)

onMounted(async () => {
  loading.value = true
  try {
    const data = await get<AiSettingsRead>('/api/v1/settings/ai')
    enabled.value = data.enabled ?? false
    baseUrl.value = data.base_url ?? 'https://api.openai.com/v1'
    apiKeySet.value = data.api_key_set ?? false
    model.value = data.model ?? ''
    receiptPrompt.value = data.receipt_prompt ?? ''
  } catch {
    // first load with no settings is fine
  } finally {
    loading.value = false
  }
})

async function handleSave() {
  saving.value = true
  message.value = ''
  testResult.value = null
  try {
    // api_key semantics: null=keep, ""=clear, non-empty=update (mirrors SMTP password)
    let apiKeyPayload: string | null = null
    if (clearApiKey.value) {
      apiKeyPayload = ''
    } else if (apiKey.value) {
      apiKeyPayload = apiKey.value
    }

    const data = await put<AiSettingsRead>('/api/v1/settings/ai', {
      enabled: enabled.value,
      base_url: baseUrl.value,
      api_key: apiKeyPayload,
      model: model.value,
      // always send a string: "" clears the custom prompt → backend falls back to DEFAULT_RECEIPT_PROMPT
      receipt_prompt: receiptPrompt.value,
    })
    apiKeySet.value = data.api_key_set ?? false
    apiKey.value = ''
    clearApiKey.value = false
    message.value = t('settings.ai.saveSuccess')
    messageType.value = 'success'
  } catch (e: unknown) {
    const err = e as { message?: string }
    message.value = err.message || t('settings.ai.saveFailed')
    messageType.value = 'error'
  } finally {
    saving.value = false
  }
}

async function handleTest() {
  testing.value = true
  message.value = ''
  testResult.value = null
  try {
    // Send current form values as the test config (mirrors SMTP test)
    let apiKeyPayload: string | null = null
    if (clearApiKey.value) {
      apiKeyPayload = ''
    } else if (apiKey.value) {
      apiKeyPayload = apiKey.value
    }

    const result = await post<AiTestResult>('/api/v1/settings/ai/test', {
      enabled: enabled.value,
      base_url: baseUrl.value,
      api_key: apiKeyPayload,
      model: model.value,
      receipt_prompt: receiptPrompt.value,
    })
    testResult.value = result
    if (result.ok) {
      message.value = result.multimodal
        ? t('settings.ai.testSuccessMultimodal')
        : t('settings.ai.testSuccessNoVision')
      messageType.value = 'success'
    } else {
      message.value = result.detail || t('settings.ai.testFailed')
      messageType.value = 'error'
    }
  } catch (e: unknown) {
    const err = e as { message?: string }
    message.value = err.message || t('settings.ai.testFailed')
    messageType.value = 'error'
  } finally {
    testing.value = false
  }
}

function handleResetPrompt() {
  receiptPrompt.value = ''
  message.value = t('settings.ai.promptReset')
  messageType.value = 'info'
}
</script>

<template>
  <n-spin :show="loading">
    <n-form label-placement="left" label-width="130" @submit.prevent="handleSave">
      <n-form-item :label="t('settings.ai.enabled')">
        <n-switch v-model:value="enabled" />
      </n-form-item>

      <n-form-item :label="t('settings.ai.baseUrl')">
        <n-input
          v-model:value="baseUrl"
          :placeholder="t('settings.ai.baseUrlPlaceholder')"
        />
      </n-form-item>

      <n-form-item :label="t('settings.ai.model')">
        <n-input
          v-model:value="model"
          :placeholder="t('settings.ai.modelPlaceholder')"
        />
      </n-form-item>

      <n-form-item :label="t('settings.ai.apiKey')">
        <n-input
          v-model:value="apiKey"
          type="password"
          show-password-on="click"
          :disabled="clearApiKey"
          :placeholder="apiKeySet ? t('settings.ai.apiKeyPlaceholder') : t('settings.ai.apiKeyPlaceholderNew')"
        />
        <template #feedback>
          <n-checkbox v-if="apiKeySet" v-model:checked="clearApiKey" style="font-size: 12px">
            {{ t('settings.ai.clearApiKey') }}
          </n-checkbox>
        </template>
      </n-form-item>

      <n-form-item :label="t('settings.ai.receiptPrompt')">
        <div style="width: 100%">
          <n-input
            v-model:value="receiptPrompt"
            type="textarea"
            :rows="5"
            :placeholder="t('settings.ai.receiptPromptPlaceholder')"
          />
          <n-button
            size="tiny"
            style="margin-top: 4px"
            @click="handleResetPrompt"
          >
            {{ t('settings.ai.promptResetBtn') }}
          </n-button>
        </div>
      </n-form-item>

      <n-alert v-if="message" :type="messageType" style="margin-bottom: 16px">
        {{ message }}
      </n-alert>

      <!-- Test result detail -->
      <n-alert
        v-if="testResult"
        :type="testResult.ok ? 'success' : 'error'"
        style="margin-bottom: 16px"
      >
        <div>
          <div><strong>{{ t('settings.ai.testConnected') }}:</strong> {{ testResult.ok ? t('settings.ai.yes') : t('settings.ai.no') }}</div>
          <div><strong>{{ t('settings.ai.testMultimodal') }}:</strong> {{ testResult.multimodal ? t('settings.ai.yes') : t('settings.ai.no') }}</div>
          <div v-if="testResult.detail"><strong>{{ t('settings.ai.testDetail') }}:</strong> {{ testResult.detail }}</div>
        </div>
      </n-alert>

      <n-space>
        <!-- Prod-build safe: separate v-if/v-else for dynamic loading buttons -->
        <n-button v-if="saving" type="primary" loading>{{ t('settings.ai.save') }}</n-button>
        <n-button v-else type="primary" attr-type="submit">{{ t('settings.ai.save') }}</n-button>

        <n-button v-if="testing" loading>{{ t('settings.ai.test') }}</n-button>
        <n-button v-else @click="handleTest">{{ t('settings.ai.test') }}</n-button>
      </n-space>
    </n-form>
  </n-spin>
</template>
