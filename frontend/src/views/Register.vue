<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import { useI18n } from 'vue-i18n'
import { useTheme } from '../composables/useTheme'

const router = useRouter()
const auth = useAuthStore()
const { t } = useI18n()
const { toggle } = useTheme()

const email = ref('')
const password = ref('')
const confirmPassword = ref('')
const errorMsg = ref('')
const loading = ref(false)

async function handleRegister() {
  errorMsg.value = ''
  if (password.value !== confirmPassword.value) {
    errorMsg.value = t('auth.passwordMismatch')
    return
  }
  if (password.value.length < 8) {
    errorMsg.value = t('auth.passwordTooShort')
    return
  }

  loading.value = true
  try {
    await auth.register(email.value, password.value)
    router.push('/login')
  } catch (e: unknown) {
    const err = e as { status?: number; message?: string }
    if (err.status === 403) {
      errorMsg.value = t('auth.registrationClosed')
    } else {
      errorMsg.value = err.message || t('auth.registerFailed')
    }
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
        <p>{{ t('auth.registerTitle') }}</p>
      </div>

      <n-form @submit.prevent="handleRegister">
        <n-form-item :label="t('auth.email')">
          <n-input
            v-model:value="email"
            type="text"
            inputmode="email"
            :input-props="{ autocomplete: 'email', name: 'email' }"
            :placeholder="t('auth.emailPlaceholder')"
          />
        </n-form-item>

        <n-form-item :label="t('auth.password')">
          <n-input
            v-model:value="password"
            type="password"
            show-password-on="click"
            :input-props="{ autocomplete: 'new-password', name: 'new-password' }"
            :placeholder="t('auth.passwordPlaceholder')"
          />
        </n-form-item>

        <n-form-item :label="t('auth.confirmPassword')">
          <n-input
            v-model:value="confirmPassword"
            type="password"
            show-password-on="click"
            :input-props="{ autocomplete: 'new-password', name: 'confirm-password' }"
            :placeholder="t('auth.confirmPasswordPlaceholder')"
          />
        </n-form-item>

        <n-alert v-if="errorMsg" type="error" style="margin-bottom: 16px">
          {{ errorMsg }}
        </n-alert>

        <n-button type="primary" block :loading="loading" attr-type="submit">
          {{ t('auth.register') }}
        </n-button>
      </n-form>

      <div class="auth-footer">
        <n-button text @click="toggle">
          {{ t('theme.toggle') }}
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
