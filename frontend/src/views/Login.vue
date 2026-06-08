<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import { useI18n } from 'vue-i18n'
import QRCode from 'qrcode'

const router = useRouter()
const auth = useAuthStore()
const { t } = useI18n()

type LoginStep = 'password' | 'mfa_setup' | 'mfa_verify'

const step = ref<LoginStep>('password')
const email = ref('')
const password = ref('')
const errorMsg = ref('')
const loading = ref(false)

// MFA setup state
const mfaSecret = ref('')
const mfaOtpauthUri = ref('')
const qrDataUrl = ref('')
const mfaCode = ref('')

async function handleLogin() {
  errorMsg.value = ''
  loading.value = true
  try {
    const next = await auth.login(email.value, password.value)
    if (next === 'mfa_setup') {
      step.value = 'mfa_setup'
      await loadMfaSetup()
    } else {
      step.value = 'mfa_verify'
    }
  } catch (e: unknown) {
    const err = e as { message?: string }
    errorMsg.value = err.message || t('auth.loginFailed')
  } finally {
    loading.value = false
  }
}

async function loadMfaSetup() {
  loading.value = true
  try {
    const result = await auth.mfaSetup()
    mfaSecret.value = result.secret
    mfaOtpauthUri.value = result.otpauth_uri
    qrDataUrl.value = await QRCode.toDataURL(result.otpauth_uri, { width: 256 })
  } catch (e: unknown) {
    const err = e as { message?: string }
    errorMsg.value = err.message || t('auth.mfaSetupFailed')
  } finally {
    loading.value = false
  }
}

async function handleMfaVerify() {
  errorMsg.value = ''
  if (!mfaCode.value) {
    errorMsg.value = t('auth.mfaCodeRequired')
    return
  }
  loading.value = true
  try {
    await auth.mfaVerify(mfaCode.value)
    // Refresh bootstrap to get the latest onboarding_completed status
    await auth.fetchBootstrap()
    // Redirect based on onboarding state
    if (auth.bootstrap?.onboarding_completed) {
      router.push('/dashboard')
    } else {
      router.push('/onboarding')
    }
  } catch (e: unknown) {
    const err = e as { message?: string }
    errorMsg.value = err.message || t('auth.mfaVerifyFailed')
  } finally {
    loading.value = false
  }
}

function copyText(text: string) {
  navigator.clipboard.writeText(text)
}
</script>

<template>
  <div class="auth-page">
    <div class="auth-card">
      <div class="auth-header">
        <h1>{{ t('app.title') }}</h1>
        <p>
          {{
            step === 'password'
              ? t('auth.loginTitle')
              : step === 'mfa_setup'
                ? t('auth.mfaSetupTitle')
                : t('auth.mfaVerifyTitle')
          }}
        </p>
      </div>

      <!-- Step 1: Email + Password -->
      <n-form v-if="step === 'password'" @submit.prevent="handleLogin">
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
            :input-props="{ autocomplete: 'current-password', name: 'password' }"
            :placeholder="t('auth.passwordPlaceholder')"
            @keyup.enter="handleLogin"
          />
        </n-form-item>

        <n-alert v-if="errorMsg" type="error" style="margin-bottom: 16px">
          {{ errorMsg }}
        </n-alert>

        <n-button type="primary" block :loading="loading" attr-type="submit">
          {{ t('auth.login') }}
        </n-button>

        <div style="text-align: center; margin-top: 12px">
          <n-button text @click="router.push('/forgot-password')">
            {{ t('auth.forgotPassword') }}
          </n-button>
        </div>
      </n-form>

      <!-- Step 2a: MFA Setup (first-time binding) -->
      <div v-else-if="step === 'mfa_setup'">
        <n-alert type="info" style="margin-bottom: 16px">
          {{ t('auth.mfaSetupInstructions') }}
        </n-alert>

        <div class="qr-container">
          <img v-if="qrDataUrl" :src="qrDataUrl" alt="TOTP QR Code" class="qr-image" />
        </div>

        <n-collapse style="margin: 16px 0">
          <n-collapse-item :title="t('auth.mfaManualEntry')" name="manual">
            <div class="manual-section">
              <span class="manual-label">Secret:</span>
              <div class="copy-row">
                <code class="copy-value">{{ mfaSecret }}</code>
                <n-button size="tiny" @click="copyText(mfaSecret)">
                  {{ t('auth.copy') }}
                </n-button>
              </div>
            </div>
            <div class="manual-section" style="margin-top: 8px">
              <span class="manual-label">URI:</span>
              <div class="copy-row">
                <code class="copy-value copy-value--uri">{{ mfaOtpauthUri }}</code>
                <n-button size="tiny" @click="copyText(mfaOtpauthUri)">
                  {{ t('auth.copy') }}
                </n-button>
              </div>
            </div>
          </n-collapse-item>
        </n-collapse>

        <n-form @submit.prevent="handleMfaVerify">
          <n-form-item :label="t('auth.mfaCode')">
            <n-input
              v-model:value="mfaCode"
              :placeholder="t('auth.mfaCodePlaceholder')"
              :input-props="{
                autocomplete: 'one-time-code',
                inputmode: 'numeric',
                pattern: '[0-9]*',
              }"
              @keyup.enter="handleMfaVerify"
            />
          </n-form-item>

          <n-alert v-if="errorMsg" type="error" style="margin-bottom: 16px">
            {{ errorMsg }}
          </n-alert>

          <n-button type="primary" block :loading="loading" attr-type="submit">
            {{ t('auth.mfaVerify') }}
          </n-button>
        </n-form>
      </div>

      <!-- Step 2b: MFA Verify (subsequent login) -->
      <n-form v-else-if="step === 'mfa_verify'" @submit.prevent="handleMfaVerify">
        <n-alert type="info" style="margin-bottom: 16px">
          {{ t('auth.mfaVerifyInstructions') }}
        </n-alert>

        <n-form-item :label="t('auth.mfaCode')">
          <n-input
            v-model:value="mfaCode"
            :placeholder="t('auth.mfaCodePlaceholder')"
            :input-props="{
              autocomplete: 'one-time-code',
              inputmode: 'numeric',
              pattern: '[0-9]*',
            }"
            @keyup.enter="handleMfaVerify"
          />
        </n-form-item>

        <n-alert v-if="errorMsg" type="error" style="margin-bottom: 16px">
          {{ errorMsg }}
        </n-alert>

        <n-button type="primary" block :loading="loading" attr-type="submit">
          {{ t('auth.mfaVerify') }}
        </n-button>
      </n-form>
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

.qr-container {
  display: flex;
  justify-content: center;
  margin-bottom: 8px;
}

.qr-image {
  border-radius: 8px;
}

.manual-label {
  display: block;
  font-size: 12px;
  opacity: 0.6;
  margin-bottom: 4px;
}

.copy-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.copy-value {
  flex: 1;
  font-size: 12px;
  word-break: break-all;
  background: var(--n-color-embedded);
  padding: 4px 8px;
  border-radius: 4px;
}

.copy-value--uri {
  font-size: 11px;
}
</style>
