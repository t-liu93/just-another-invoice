<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useCompanyStore } from '../../stores/company'
import { useI18n } from 'vue-i18n'
import AppHeader from '../../components/AppHeader.vue'
import { get, put } from '../../api/http'

const companyStore = useCompanyStore()
const { t } = useI18n()

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

// Logo state
const uploadingLogo = ref(false)
const logoError = ref('')

// Numbering state
const numberingTemplate = ref('{{SERIES:INV}}-{{SEQUENCE:6}}')
const numberingSequenceStart = ref(1)
const numberingLoaded = ref(false)
const numberingSaving = ref(false)
const numberingMessage = ref('')
const numberingMessageType = ref<'success' | 'error'>('success')

const loadError = computed(() => companyStore.error)
const hasLogo = computed(() => companyStore.company?.has_logo ?? false)
const logoUrl = computed(() => companyStore.company?.logo_url ?? null)

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

  // Load numbering config
  try {
    const data = await get<{ template: string; sequence_start: number }>('/api/v1/settings/numbering')
    if (data) {
      numberingTemplate.value = data.template
      numberingSequenceStart.value = data.sequence_start
      numberingLoaded.value = true
    }
  } catch {
    // Use defaults
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

async function handleLogoUpload(event: Event) {
  const input = event.target as HTMLInputElement
  if (!input.files?.length) return
  const file = input.files[0]

  uploadingLogo.value = true
  logoError.value = ''
  try {
    await companyStore.uploadLogo(file)
  } catch {
    logoError.value = companyStore.error || t('settings.company.logoUploadFailed')
  } finally {
    uploadingLogo.value = false
    // Reset input so the same file can be re-selected.
    input.value = ''
  }
}

async function handleLogoDelete() {
  uploadingLogo.value = true
  logoError.value = ''
  try {
    await companyStore.deleteLogo()
  } catch {
    logoError.value = companyStore.error || t('settings.company.logoDeleteFailed')
  } finally {
    uploadingLogo.value = false
  }
}

async function handleSaveNumbering() {
  numberingSaving.value = true
  numberingMessage.value = ''
  try {
    const result = await put<{ template: string; sequence_start: number }>(
      '/api/v1/settings/numbering',
      {
        template: numberingTemplate.value,
        sequence_start: numberingSequenceStart.value,
      },
    )
    numberingTemplate.value = result.template
    numberingSequenceStart.value = result.sequence_start
    numberingMessage.value = t('settings.numbering.saveSuccess')
    numberingMessageType.value = 'success'
  } catch (e: unknown) {
    const err = e as { message?: string }
    numberingMessage.value = err.message || t('settings.numbering.saveFailed')
    numberingMessageType.value = 'error'
  } finally {
    numberingSaving.value = false
  }
}
</script>

<template>
  <div class="settings-page">
    <n-layout>
      <AppHeader />

      <n-layout-content class="app-content">
        <div class="company-profile">
          <n-card :title="t('settings.company.title')">
            <n-spin :show="loading">
              <n-alert v-if="loadError" type="error" style="margin-bottom: 16px">
                {{ loadError }}
              </n-alert>

              <!-- Logo section -->
              <n-divider>{{ t('settings.company.logoSection') }}</n-divider>
              <div class="logo-section">
                <div class="logo-preview">
                  <img
                    v-if="hasLogo && logoUrl"
                    :src="logoUrl"
                    alt="Company Logo"
                    class="logo-image"
                  />
                  <div v-else class="logo-placeholder">
                    {{ t('settings.company.noLogo') }}
                  </div>
                </div>
                <div class="logo-actions">
                  <n-button
                    type="primary"
                    size="small"
                    :loading="uploadingLogo"
                    @click="($refs.logoInput as HTMLInputElement | undefined)?.click()"
                  >
                    {{ hasLogo ? t('settings.company.replaceLogo') : t('settings.company.uploadLogo') }}
                  </n-button>
                  <n-button
                    v-if="hasLogo"
                    type="error"
                    size="small"
                    :loading="uploadingLogo"
                    @click="handleLogoDelete"
                  >
                    {{ t('settings.company.deleteLogo') }}
                  </n-button>
                  <input
                    ref="logoInput"
                    type="file"
                    accept="image/png,image/jpeg,image/webp,image/svg+xml"
                    style="display: none"
                    @change="handleLogoUpload"
                  />
                  <n-text depth="3" style="font-size: 12px">
                    PNG, JPEG, WebP, SVG · max 512 KB
                  </n-text>
                </div>
              </div>
              <n-alert
                v-if="logoError"
                type="error"
                style="margin-top: 12px"
                closable
                @close="logoError = ''"
              >
                {{ logoError }}
              </n-alert>

              <n-divider>{{ t('settings.company.identitySection') }}</n-divider>

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

              <n-divider>{{ t('settings.numbering.section') }}</n-divider>

              <n-alert type="info" style="margin-bottom: 16px" closable>
                {{ t('settings.numbering.m5hint') }}
              </n-alert>

              <n-form label-placement="left" label-width="140" :disabled="!companyStore.hasCompany" @submit.prevent="handleSaveNumbering">
                <n-form-item :label="t('settings.numbering.template')">
                  <n-input v-model:value="numberingTemplate" placeholder="{{SERIES:INV}}-{{SEQUENCE:6}}" />
                </n-form-item>

                <n-form-item :label="t('settings.numbering.sequenceStart')">
                  <n-input-number v-model:value="numberingSequenceStart" :min="1" style="width: 100%" />
                </n-form-item>

                <n-alert v-if="numberingMessage" :type="numberingMessageType" style="margin-bottom: 16px">
                  {{ numberingMessage }}
                </n-alert>

                <n-button type="primary" :loading="numberingSaving" :disabled="!companyStore.hasCompany" attr-type="submit">
                  {{ t('settings.numbering.save') }}
                </n-button>
              </n-form>
            </n-spin>
          </n-card>
        </div>
      </n-layout-content>
    </n-layout>
  </div>
</template>

<style scoped>
.app-content {
  min-height: calc(100vh - 57px);
}

.company-profile {
  max-width: 640px;
  margin: 24px auto;
  padding: 0 24px;
}

.logo-section {
  display: flex;
  align-items: flex-start;
  gap: 16px;
  margin-bottom: 16px;
}

.logo-preview {
  width: 80px;
  height: 80px;
  border: 1px dashed var(--n-border-color);
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
  flex-shrink: 0;
  background: var(--n-color);
}

.logo-image {
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
}

.logo-placeholder {
  font-size: 12px;
  color: var(--n-text-color-3);
  text-align: center;
  padding: 8px;
}

.logo-actions {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
</style>
