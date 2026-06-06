<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../../stores/auth'
import { useCompanyStore } from '../../stores/company'
import { useI18n } from 'vue-i18n'
import { useTheme } from '../../composables/useTheme'
import { SunnyOutline, MoonOutline, GlobeOutline, LogOutOutline } from '@vicons/ionicons5'
import { NIcon } from 'naive-ui'
import { computed } from 'vue'

const router = useRouter()
const auth = useAuthStore()
const companyStore = useCompanyStore()
const { t, locale } = useI18n()
const { toggle, preference } = useTheme()

const isDark = computed(() => {
  const p = preference.value
  if (p === 'system') {
    return window.matchMedia('(prefers-color-scheme: dark)').matches
  }
  return p === 'dark'
})

// Form fields
const name = ref('')
const vatId = ref('')
const cocNumber = ref('')
const email = ref('')
const phone = ref('')
const website = ref('')
const addressLine1 = ref('')
const addressLine2 = ref('')
const postalCode = ref('')
const city = ref('')
const countryCode = ref('')
const baseCurrency = ref('EUR')

const loading = ref(false)
const saving = ref(false)
const message = ref('')
const messageType = ref<'success' | 'error'>('success')

const loadError = computed(() => companyStore.error)

onMounted(async () => {
  loading.value = true
  try {
    await companyStore.fetchCompany()
    if (companyStore.company) {
      const c = companyStore.company
      name.value = c.name ?? ''
      vatId.value = c.vat_id ?? ''
      cocNumber.value = c.coc_number ?? ''
      email.value = c.email ?? ''
      phone.value = c.phone ?? ''
      website.value = c.website ?? ''
      addressLine1.value = c.address_line1 ?? ''
      addressLine2.value = c.address_line2 ?? ''
      postalCode.value = c.postal_code ?? ''
      city.value = c.city ?? ''
      countryCode.value = c.country_code ?? ''
      baseCurrency.value = c.base_currency ?? 'EUR'
    }
  } catch {
    // fetchCompany sets companyStore.error on real errors; component reads it.
  } finally {
    loading.value = false
  }
})

async function handleSave() {
  if (loadError.value) return
  saving.value = true
  message.value = ''
  try {
    await companyStore.saveCompany({
      name: name.value,
      vat_id: vatId.value || null,
      coc_number: cocNumber.value || null,
      email: email.value || null,
      phone: phone.value || null,
      website: website.value || null,
      address_line1: addressLine1.value || null,
      address_line2: addressLine2.value || null,
      postal_code: postalCode.value || null,
      city: city.value || null,
      country_code: countryCode.value || null,
      base_currency: baseCurrency.value,
    })
    message.value = t('settings.company.saveSuccess')
    messageType.value = 'success'
  } catch (e: unknown) {
    const err = e as { message?: string }
    message.value = err.message || t('settings.company.saveFailed')
    messageType.value = 'error'
  } finally {
    saving.value = false
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
            ← {{ t('app.title') }}
          </n-button>
        </div>
        <div class="header-right">
          <n-button quaternary size="small" @click="router.push('/settings/smtp')">
            <template #icon>
              <n-icon><SettingsOutline /></n-icon>
            </template>
          </n-button>
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
        <div class="company-profile">
          <n-card :title="t('settings.company.title')">
            <n-spin :show="loading">
              <n-alert v-if="loadError" type="error" style="margin-bottom: 16px">
                {{ loadError }}
              </n-alert>
              <n-form label-placement="left" label-width="140" :disabled="!!loadError" @submit.prevent="handleSave">
                <n-form-item :label="t('settings.company.name')" required>
                  <n-input v-model:value="name" :placeholder="t('settings.company.namePlaceholder')" />
                </n-form-item>

                <n-form-item :label="t('settings.company.vatId')">
                  <n-input v-model:value="vatId" placeholder="NL123456789B01" />
                </n-form-item>

                <n-form-item :label="t('settings.company.cocNumber')">
                  <n-input v-model:value="cocNumber" :placeholder="t('settings.company.cocNumberPlaceholder')" />
                </n-form-item>

                <n-form-item :label="t('settings.company.email')">
                  <n-input v-model:value="email" placeholder="info@company.nl" />
                </n-form-item>

                <n-form-item :label="t('settings.company.phone')">
                  <n-input v-model:value="phone" placeholder="+31 ..." />
                </n-form-item>

                <n-form-item :label="t('settings.company.website')">
                  <n-input v-model:value="website" placeholder="https://..." />
                </n-form-item>

                <n-divider>{{ t('settings.company.addressSection') }}</n-divider>

                <n-form-item :label="t('settings.company.addressLine1')">
                  <n-input v-model:value="addressLine1" :placeholder="t('settings.company.addressLine1Placeholder')" />
                </n-form-item>

                <n-form-item :label="t('settings.company.addressLine2')">
                  <n-input v-model:value="addressLine2" />
                </n-form-item>

                <n-form-item :label="t('settings.company.postalCode')">
                  <n-input v-model:value="postalCode" />
                </n-form-item>

                <n-form-item :label="t('settings.company.city')">
                  <n-input v-model:value="city" />
                </n-form-item>

                <n-form-item :label="t('settings.company.countryCode')">
                  <n-input v-model:value="countryCode" placeholder="NL" :maxlength="2" />
                </n-form-item>

                <n-divider>{{ t('settings.company.financialSection') }}</n-divider>

                <n-form-item :label="t('settings.company.baseCurrency')" required>
                  <n-input v-model:value="baseCurrency" placeholder="EUR" :maxlength="3" />
                </n-form-item>

                <n-alert v-if="message" :type="messageType" style="margin-bottom: 16px">
                  {{ message }}
                </n-alert>

                <n-button type="primary" :loading="saving" :disabled="!!loadError" attr-type="submit">
                  {{ t('settings.company.save') }}
                </n-button>
              </n-form>
            </n-spin>
          </n-card>
        </div>
      </n-layout-content>
    </n-layout>
  </div>
</template>

<script lang="ts">
import { SettingsOutline } from '@vicons/ionicons5'
export default {
  components: { SettingsOutline },
}
</script>

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

.company-profile {
  max-width: 640px;
  margin: 24px auto;
  padding: 0 24px;
}
</style>
