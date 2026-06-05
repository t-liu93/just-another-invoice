<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../../stores/auth'
import { useI18n } from 'vue-i18n'
import { useTheme } from '../../composables/useTheme'
import { get, put, post } from '../../api/http'
import type { components } from '../../api/schema'
import { SunnyOutline, MoonOutline, GlobeOutline, LogOutOutline } from '@vicons/ionicons5'
import { NIcon } from 'naive-ui'
import { computed } from 'vue'

type SmtpSettingsRead = components['schemas']['SmtpSettingsRead']

const router = useRouter()
const auth = useAuthStore()
const { t, locale } = useI18n()
const { toggle, preference } = useTheme()

const isDark = computed(() => {
  const p = preference.value
  if (p === 'system') {
    return window.matchMedia('(prefers-color-scheme: dark)').matches
  }
  return p === 'dark'
})

const host = ref('')
const port = ref(587)
const username = ref('')
const password = ref('')
const passwordSet = ref(false)
const fromEmail = ref('')
const fromName = ref('')
const useTls = ref(true)
const useSsl = ref(false)

const loading = ref(false)
const saving = ref(false)
const testing = ref(false)
const clearPassword = ref(false)
const message = ref('')
const messageType = ref<'success' | 'error'>('success')

onMounted(async () => {
  loading.value = true
  try {
    const data = await get<SmtpSettingsRead>('/api/v1/settings/smtp')
    host.value = data.host ?? ''
    port.value = data.port ?? 587
    username.value = data.username ?? ''
    passwordSet.value = data.password_set ?? false
    fromEmail.value = data.from_email ?? ''
    fromName.value = data.from_name ?? ''
    useTls.value = data.use_tls ?? true
    useSsl.value = data.use_ssl ?? false
  } catch {
    // ignore – may be first load with no settings
  } finally {
    loading.value = false
  }
})

async function handleSave() {
  saving.value = true
  message.value = ''
  try {
    // password logic:
    // - clearPassword checked → send "" to clear saved password
    // - password field has input → send the new password
    // - password field empty & not clearing → send null to keep existing
    let passwordPayload: string | null = null
    if (clearPassword.value) {
      passwordPayload = ''
    } else if (password.value) {
      passwordPayload = password.value
    }
    const data = await put<SmtpSettingsRead>('/api/v1/settings/smtp', {
      host: host.value,
      port: port.value,
      username: username.value,
      password: passwordPayload,
      from_email: fromEmail.value,
      from_name: fromName.value,
      use_tls: useTls.value,
      use_ssl: useSsl.value,
    })
    passwordSet.value = data.password_set ?? false
    password.value = ''
    clearPassword.value = false
    message.value = t('settings.smtp.saveSuccess')
    messageType.value = 'success'
  } catch (e: unknown) {
    const err = e as { message?: string }
    message.value = err.message || t('settings.smtp.saveFailed')
    messageType.value = 'error'
  } finally {
    saving.value = false
  }
}

async function handleTestEmail() {
  testing.value = true
  message.value = ''
  try {
    await post<{ status: string }>('/api/v1/settings/smtp/test', {})
    message.value = t('settings.smtp.testSuccess')
    messageType.value = 'success'
  } catch (e: unknown) {
    const err = e as { message?: string }
    message.value = err.message || t('settings.smtp.testFailed')
    messageType.value = 'error'
  } finally {
    testing.value = false
  }
}

async function handleLogout() {
  await auth.logout()
  router.push('/login')
}
</script>

<template>
  <div class="settings-page">
    <n-layout>
      <n-layout-header bordered class="app-header">
        <div class="header-left">
          <n-button quaternary size="small" @click="router.push('/dashboard')">
            ← {{ t('dashboard.welcome').split(' ').slice(0, 2).join(' ') }}
          </n-button>
        </div>
        <div class="header-right">
          <n-button quaternary size="small" @click="toggle">
            <template #icon>
              <n-icon><SunnyOutline v-if="isDark" /><MoonOutline v-else /></n-icon>
            </template>
          </n-button>
          <n-button quaternary size="small" @click="locale = locale === 'en' ? 'zh' : 'en'">
            <template #icon>
              <n-icon><GlobeOutline /></n-icon>
            </template>
            {{ locale === 'en' ? '中文' : 'EN' }}
          </n-button>
          <n-tag v-if="auth.user" size="small" type="info">
            {{ auth.user.email }}
          </n-tag>
          <n-button quaternary size="small" type="error" @click="handleLogout">
            <template #icon>
              <n-icon><LogOutOutline /></n-icon>
            </template>
          </n-button>
        </div>
      </n-layout-header>

      <n-layout-content class="app-content">
        <div class="smtp-settings">
          <n-card :title="t('settings.smtp.title')">
            <n-spin :show="loading">
              <n-form label-placement="left" label-width="120" @submit.prevent="handleSave">
                <n-form-item :label="t('settings.smtp.host')">
                  <n-input v-model:value="host" :placeholder="t('settings.smtp.hostPlaceholder')" />
                </n-form-item>

                <n-form-item :label="t('settings.smtp.port')">
                  <n-input-number v-model:value="port" :min="1" :max="65535" style="width: 100%" />
                </n-form-item>

                <n-form-item :label="t('settings.smtp.username')">
                  <n-input v-model:value="username" :placeholder="t('settings.smtp.usernamePlaceholder')" />
                </n-form-item>

                <n-form-item :label="t('settings.smtp.password')">
                  <n-input
                    v-model:value="password"
                    type="password"
                    show-password-on="click"
                    :disabled="clearPassword"
                    :placeholder="passwordSet ? t('settings.smtp.passwordPlaceholder') : t('settings.smtp.passwordPlaceholderNew')"
                  />
                  <template #feedback>
                    <n-checkbox v-if="passwordSet" v-model:checked="clearPassword" style="font-size: 12px">
                      {{ t('settings.smtp.clearPassword') }}
                    </n-checkbox>
                  </template>
                </n-form-item>

                <n-form-item :label="t('settings.smtp.fromEmail')">
                  <n-input v-model:value="fromEmail" placeholder="noreply@example.com" />
                </n-form-item>

                <n-form-item :label="t('settings.smtp.fromName')">
                  <n-input v-model:value="fromName" :placeholder="t('settings.smtp.fromNamePlaceholder')" />
                </n-form-item>

                <n-form-item :label="t('settings.smtp.security')">
                  <n-space>
                    <n-checkbox v-model:checked="useTls">
                      {{ t('settings.smtp.useTls') }}
                    </n-checkbox>
                    <n-checkbox v-model:checked="useSsl">
                      {{ t('settings.smtp.useSsl') }}
                    </n-checkbox>
                  </n-space>
                </n-form-item>

                <n-alert v-if="message" :type="messageType" style="margin-bottom: 16px">
                  {{ message }}
                </n-alert>

                <n-space>
                  <n-button type="primary" :loading="saving" attr-type="submit">
                    {{ t('settings.smtp.save') }}
                  </n-button>
                  <n-button :loading="testing" @click="handleTestEmail">
                    {{ t('settings.smtp.testEmail') }}
                  </n-button>
                </n-space>
              </n-form>
            </n-spin>
          </n-card>
        </div>
      </n-layout-content>
    </n-layout>
  </div>
</template>

<style scoped>
.app-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 24px;
}

.header-left h2 {
  margin: 0;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 8px;
}

.app-content {
  min-height: calc(100vh - 57px);
}

.smtp-settings {
  max-width: 600px;
  margin: 24px auto;
  padding: 0 24px;
}
</style>
