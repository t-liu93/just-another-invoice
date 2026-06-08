<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import { useCompanyStore } from '../stores/company'
import { useI18n } from 'vue-i18n'

const router = useRouter()
const route = useRoute()
const auth = useAuthStore()
const companyStore = useCompanyStore()
const { t } = useI18n()

type OnboardingStep = 'company' | 'smtp' | 'done'

const step = ref<OnboardingStep>('company')

// Company form fields
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

const saving = ref(false)
const errorMsg = ref('')

const canProceed = computed(() => name.value.trim().length > 0 && baseCurrency.value.trim().length === 3)

async function handleSaveCompany() {
  if (!canProceed.value) return
  saving.value = true
  errorMsg.value = ''
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
    // Refresh bootstrap so onboarding_completed updates
    await auth.fetchBootstrap()
    step.value = 'smtp'
  } catch (e: unknown) {
    const err = e as { message?: string }
    errorMsg.value = err.message || t('onboarding.saveFailed')
  } finally {
    saving.value = false
  }
}

function handleSkipSmtp() {
  step.value = 'done'
}

function handleConfigureSmtp() {
  router.push({ name: 'smtp-settings', query: { from: 'onboarding' } })
}

async function handleFinish() {
  router.push('/dashboard')
}

onMounted(() => {
  if (auth.bootstrap?.onboarding_completed) {
    if (route.query.resume === 'done') {
      step.value = 'done'
      return
    }
    router.push('/dashboard')
  }
})
</script>

<template>
  <div class="onboarding-page">
    <div class="onboarding-card">
      <div class="onboarding-header">
        <h1>{{ t('app.title') }}</h1>
        <n-steps :current="step === 'company' ? 1 : step === 'smtp' ? 2 : 3" :size="'small'">
          <n-step :title="t('onboarding.stepCompany')" />
          <n-step :title="t('onboarding.stepEmail')" />
          <n-step :title="t('onboarding.stepDone')" />
        </n-steps>
      </div>

      <!-- Step 1: Company Profile (mandatory) -->
      <template v-if="step === 'company'">
        <n-alert type="info" style="margin-bottom: 16px">
          {{ t('onboarding.companyHint') }}
        </n-alert>

        <n-form label-placement="left" label-width="140" @submit.prevent="handleSaveCompany">
          <n-form-item :label="t('onboarding.companyName')" required>
            <n-input v-model:value="name" :placeholder="t('onboarding.companyNamePlaceholder')" />
          </n-form-item>

          <n-form-item :label="t('onboarding.vatId')">
            <n-input v-model:value="vatId" placeholder="NL123456789B01" />
          </n-form-item>

          <n-form-item :label="t('onboarding.cocNumber')">
            <n-input v-model:value="cocNumber" placeholder="12345678" />
          </n-form-item>

          <n-form-item :label="t('onboarding.email')">
            <n-input v-model:value="email" placeholder="info@company.nl" />
          </n-form-item>

          <n-form-item :label="t('onboarding.phone')">
            <n-input v-model:value="phone" placeholder="+31 ..." />
          </n-form-item>

          <n-form-item :label="t('onboarding.website')">
            <n-input v-model:value="website" placeholder="https://..." />
          </n-form-item>

          <n-divider>{{ t('onboarding.addressSection') }}</n-divider>

          <n-form-item :label="t('onboarding.addressLine1')">
            <n-input v-model:value="addressLine1" placeholder="Keizersgracht 1" />
          </n-form-item>

          <n-form-item :label="t('onboarding.addressLine2')">
            <n-input v-model:value="addressLine2" />
          </n-form-item>

          <n-form-item :label="t('onboarding.postalCode')">
            <n-input v-model:value="postalCode" />
          </n-form-item>

          <n-form-item :label="t('onboarding.city')">
            <n-input v-model:value="city" />
          </n-form-item>

          <n-form-item :label="t('onboarding.countryCode')">
            <n-input v-model:value="countryCode" placeholder="NL" :maxlength="2" />
          </n-form-item>

          <n-divider>{{ t('onboarding.financialSection') }}</n-divider>

          <n-form-item :label="t('onboarding.baseCurrency')" required>
            <n-input v-model:value="baseCurrency" placeholder="EUR" :maxlength="3" />
          </n-form-item>

          <n-alert v-if="errorMsg" type="error" style="margin-bottom: 16px">
            {{ errorMsg }}
          </n-alert>

          <n-button type="primary" block :loading="saving" :disabled="!canProceed" attr-type="submit">
            {{ t('onboarding.continue') }}
          </n-button>
        </n-form>
      </template>

      <!-- Step 2: SMTP (optional, skippable) -->
      <template v-else-if="step === 'smtp'">
        <n-alert type="info" style="margin-bottom: 16px">
          {{ t('onboarding.smtpHint') }}
        </n-alert>

        <div style="display: flex; gap: 12px; margin-top: 16px">
          <n-button type="primary" @click="handleConfigureSmtp">
            {{ t('onboarding.configureSmtp') }}
          </n-button>
          <n-button @click="handleSkipSmtp">
            {{ t('onboarding.skipSmtp') }}
          </n-button>
        </div>
      </template>

      <!-- Step 3: Done -->
      <template v-else>
        <n-result status="success" :title="t('onboarding.doneTitle')" :description="t('onboarding.doneHint')">
          <template #footer>
            <n-button type="primary" @click="handleFinish">
              {{ t('onboarding.goDashboard') }}
            </n-button>
          </template>
        </n-result>
      </template>
    </div>
  </div>
</template>

<style scoped>
.onboarding-page {
  display: flex;
  justify-content: center;
  align-items: flex-start;
  min-height: 100vh;
  padding: 48px 24px;
}

.onboarding-card {
  width: 100%;
  max-width: 560px;
}

.onboarding-header {
  text-align: center;
  margin-bottom: 32px;
}

.onboarding-header h1 {
  margin: 0 0 16px;
  font-size: 24px;
}
</style>
