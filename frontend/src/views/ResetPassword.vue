<script setup lang="ts">
import { ref } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { post } from '../api/http'

const router = useRouter()
const route = useRoute()
const { t } = useI18n()

const token = (route.query.token as string) || ''
const password = ref('')
const confirmPassword = ref('')
const loading = ref(false)
const errorMsg = ref('')
const success = ref(false)

async function handleSubmit() {
  errorMsg.value = ''
  if (password.value.length < 8) {
    errorMsg.value = t('auth.passwordTooShort')
    return
  }
  if (password.value !== confirmPassword.value) {
    errorMsg.value = t('auth.passwordMismatch')
    return
  }
  if (!token) {
    errorMsg.value = t('auth.resetTokenMissing')
    return
  }

  loading.value = true
  try {
    await post('/api/v1/auth/reset-password', {
      token,
      password: password.value,
    })
    success.value = true
  } catch (e: unknown) {
    const err = e as { message?: string }
    errorMsg.value = err.message || t('auth.resetFailed')
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
        <p>{{ t('auth.resetTitle') }}</p>
      </div>

      <n-alert v-if="success" type="success" style="margin-bottom: 16px">
        {{ t('auth.resetSuccess') }}
      </n-alert>

      <n-form v-if="!success" @submit.prevent="handleSubmit">
        <n-form-item :label="t('auth.newPassword')">
          <n-input
            v-model:value="password"
            type="password"
            show-password-on="click"
            :input-props="{ autocomplete: 'new-password', name: 'password' }"
            :placeholder="t('auth.passwordPlaceholder')"
          />
        </n-form-item>

        <n-form-item :label="t('auth.confirmNewPassword')">
          <n-input
            v-model:value="confirmPassword"
            type="password"
            show-password-on="click"
            :input-props="{ autocomplete: 'new-password', name: 'confirmPassword' }"
            :placeholder="t('auth.confirmPasswordPlaceholder')"
            @keyup.enter="handleSubmit"
          />
        </n-form-item>

        <n-alert v-if="errorMsg" type="error" style="margin-bottom: 16px">
          {{ errorMsg }}
        </n-alert>

        <n-button type="primary" block :loading="loading" attr-type="submit">
          {{ t('auth.resetPassword') }}
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
