<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { post } from '../api/http'

const router = useRouter()
const { t } = useI18n()

const email = ref('')
const loading = ref(false)
const submitted = ref(false)
const errorMsg = ref('')

async function handleSubmit() {
  errorMsg.value = ''
  loading.value = true
  try {
    await post('/api/v1/auth/forgot-password', { email: email.value })
    submitted.value = true
  } catch (e: unknown) {
    const err = e as { message?: string }
    errorMsg.value = err.message || t('auth.forgotFailed')
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="auth-page">
    <div class="auth-card">
      <div class="auth-header">
        <h1>{{ t('app.title') }}</h1>
        <p>{{ t('auth.forgotTitle') }}</p>
      </div>

      <n-alert v-if="submitted" type="success" style="margin-bottom: 16px">
        {{ t('auth.forgotSent') }}
      </n-alert>

      <n-form v-if="!submitted" @submit.prevent="handleSubmit">
        <n-form-item :label="t('auth.email')">
          <n-input
            v-model:value="email"
            type="text"
            inputmode="email"
            :input-props="{ autocomplete: 'email', name: 'email' }"
            :placeholder="t('auth.emailPlaceholder')"
          />
        </n-form-item>

        <n-alert v-if="errorMsg" type="error" style="margin-bottom: 16px">
          {{ errorMsg }}
        </n-alert>

        <n-button type="primary" block :loading="loading" attr-type="submit">
          {{ t('auth.sendResetLink') }}
        </n-button>
      </n-form>

      <div style="text-align: center; margin-top: 16px">
        <n-button text @click="router.push('/login')">
          {{ t('auth.backToLogin') }}
        </n-button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.auth-page {
  display: flex;
  justify-content: center;
  align-items: center;
  min-height: 100vh;
  padding: 24px;
}

.auth-card {
  width: 100%;
  max-width: 400px;
}

.auth-header {
  text-align: center;
  margin-bottom: 32px;
}

.auth-header h1 {
  margin: 0 0 8px;
  font-size: 24px;
}

.auth-header p {
  opacity: 0.6;
  margin: 0;
}

.auth-footer {
  text-align: center;
  margin-top: 16px;
}
</style>
