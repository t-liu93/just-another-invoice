<script setup lang="ts">
/**
 * Customer edit / create page – scalar fields + billing/shipping addresses (M3 step 2).
 */
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useMessage, NButton, NSpace, NInput, NForm, NFormItem, NCard, NSpin, NAlert, NDivider, NSelect } from 'naive-ui'
import AppHeader from '../../components/AppHeader.vue'
import AddressFieldsForm, { type AddressModel } from '../../components/AddressFieldsForm.vue'
import { useCustomersStore } from '../../stores/customers'
import type { components } from '../../api/schema'

type AddressWrite = components['schemas']['AddressWrite']
type CustomerLocale = 'en' | 'zh' | null

const route = useRoute()
const router = useRouter()
const { t } = useI18n()
const message = useMessage()
const store = useCustomersStore()

const isEdit = ref(false)
const loading = ref(false)
const saving = ref(false)
const pageError = ref<string | null>(null)

// Form fields.
const name = ref('')
const contactName = ref<string | null>(null)
const companyName = ref<string | null>(null)
const email = ref<string | null>(null)
const phone = ref<string | null>(null)
const website = ref<string | null>(null)
const vatId = ref<string | null>(null)
const currency = ref<string | null>(null)
const invoicePrefix = ref<string | null>(null)
const locale = ref<CustomerLocale>(null)

// Address models.
const billingAddress = ref<AddressModel>({})
const shippingAddress = ref<AddressModel>({})

function isAddressEmpty(addr: AddressModel): boolean {
  return !addr.street && !addr.house_number && !addr.house_number_addition
    && !addr.postal_code && !addr.city && !addr.province && !addr.country_code
}

function buildAddressesPayload(): AddressWrite[] {
  const out: AddressWrite[] = []
  if (!isAddressEmpty(billingAddress.value)) {
    out.push({ type: 'BILLING', ...billingAddress.value })
  }
  if (!isAddressEmpty(shippingAddress.value)) {
    out.push({ type: 'SHIPPING', ...shippingAddress.value })
  }
  return out
}

onMounted(async () => {
  const id = route.params.id as string | undefined
  if (id && id !== 'new') {
    isEdit.value = true
    loading.value = true
    try {
      const customer = await store.fetchCustomer(id)
      name.value = customer.name
      contactName.value = customer.contact_name ?? null
      companyName.value = customer.company_name ?? null
      email.value = customer.email ?? null
      phone.value = customer.phone ?? null
      website.value = customer.website ?? null
      vatId.value = customer.vat_id ?? null
      currency.value = customer.currency ?? null
      invoicePrefix.value = customer.invoice_prefix ?? null
      locale.value = (customer.locale as CustomerLocale) ?? null

      // Populate address fields from existing data.
      for (const addr of customer.addresses ?? []) {
        const model: AddressModel = {
          street: addr.street ?? null,
          house_number: addr.house_number ?? null,
          house_number_addition: addr.house_number_addition ?? null,
          postal_code: addr.postal_code ?? null,
          city: addr.city ?? null,
          province: addr.province ?? null,
          country_code: addr.country_code ?? null,
        }
        if (addr.type === 'BILLING') billingAddress.value = model
        else if (addr.type === 'SHIPPING') shippingAddress.value = model
      }
    } catch {
      pageError.value = t('customers.loadFailed')
    } finally {
      loading.value = false
    }
  }
})

async function handleSave() {
  if (!name.value.trim()) {
    message.error(t('customers.nameRequired'))
    return
  }
  saving.value = true
  try {
    const payload = {
      name: name.value.trim(),
      contact_name: contactName.value?.trim() || null,
      company_name: companyName.value?.trim() || null,
      email: email.value?.trim() || null,
      phone: phone.value?.trim() || null,
      website: website.value?.trim() || null,
      vat_id: vatId.value?.trim() || null,
      currency: currency.value?.trim() || null,
      invoice_prefix: invoicePrefix.value?.trim() || null,
      locale: locale.value,
      addresses: buildAddressesPayload(),
    }
    if (isEdit.value) {
      await store.updateCustomer(route.params.id as string, payload)
    } else {
      await store.createCustomer(payload)
    }
    message.success(t('customers.saveSuccess'))
    router.push('/customers')
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e)
    message.error(msg || t('customers.saveFailed'))
  } finally {
    saving.value = false
  }
}

function handleCancel() {
  router.push('/customers')
}
</script>

<template>
  <div class="customer-edit-page">
    <n-layout>
      <AppHeader />

      <n-layout-content class="app-content">
        <div class="customer-edit-container">
          <h2>{{ isEdit ? t('customers.edit') : t('customers.create') }}</h2>

          <n-alert v-if="pageError" type="error" style="margin-bottom: 16px">
            {{ pageError }}
          </n-alert>

          <n-spin :show="loading">
            <n-card>
              <n-form label-placement="left" label-width="140">
                <!-- Info section -->
                <n-divider>{{ t('customers.infoSection') }}</n-divider>

                <n-form-item :label="t('customers.name')" required>
                  <n-input v-model:value="name" :placeholder="t('customers.namePlaceholder')" />
                </n-form-item>

                <n-form-item :label="t('customers.contactName')">
                  <n-input v-model:value="contactName" :placeholder="t('customers.contactNamePlaceholder')" />
                </n-form-item>

                <n-form-item :label="t('customers.companyName')">
                  <n-input v-model:value="companyName" :placeholder="t('customers.companyNamePlaceholder')" />
                </n-form-item>

                <!-- Contact section -->
                <n-divider>{{ t('customers.contactSection') }}</n-divider>

                <n-form-item :label="t('customers.email')">
                  <n-input v-model:value="email" placeholder="email@example.com" />
                </n-form-item>

                <n-form-item :label="t('customers.phone')">
                  <n-input v-model:value="phone" placeholder="+31612345678" />
                </n-form-item>

                <n-form-item :label="t('customers.website')">
                  <n-input v-model:value="website" placeholder="https://example.com" />
                </n-form-item>

                <!-- Financial section -->
                <n-divider>{{ t('customers.financialSection') }}</n-divider>

                <n-form-item :label="t('customers.vatId')">
                  <n-input v-model:value="vatId" :placeholder="t('customers.vatIdPlaceholder')" />
                </n-form-item>

                <n-form-item :label="t('customers.currency')">
                  <n-input v-model:value="currency" :placeholder="t('customers.currencyPlaceholder')" style="max-width: 200px" />
                </n-form-item>

                <n-form-item :label="t('customers.invoicePrefix')">
                  <n-input
                    v-model:value="invoicePrefix"
                    :placeholder="t('customers.invoicePrefixPlaceholder')"
                    style="max-width: 200px"
                  />
                  <n-text depth="3" style="margin-left: 8px; font-size: 12px">
                    {{ t('customers.invoicePrefixHint') }}
                  </n-text>
                </n-form-item>

                <n-form-item :label="t('customers.documentLocale')">
                  <n-select
                    v-model:value="locale"
                    :options="[
                      { label: 'English', value: 'en' },
                      { label: '中文', value: 'zh' },
                    ]"
                    :placeholder="t('customers.documentLocaleDefault')"
                    clearable
                    style="max-width: 240px"
                  />
                  <n-text depth="3" style="margin-left: 8px; font-size: 12px">
                    {{ t('customers.documentLocaleHint') }}
                  </n-text>
                </n-form-item>

                <!-- Addresses section -->
                <n-divider>{{ t('customers.addressSection') }}</n-divider>

                <!-- Billing address -->
                <p class="address-type-label">{{ t('customers.billingAddress') }}</p>
                <AddressFieldsForm
                  v-model="billingAddress"
                />

                <!-- Shipping address -->
                <p class="address-type-label">{{ t('customers.shippingAddress') }}</p>
                <AddressFieldsForm
                  v-model="shippingAddress"
                />
              </n-form>

              <n-space justify="end" style="margin-top: 16px">
                <n-button @click="handleCancel">{{ t('customers.cancel') }}</n-button>
                <n-button type="primary" :loading="saving" @click="handleSave">
                  {{ t('customers.save') }}
                </n-button>
              </n-space>
            </n-card>
          </n-spin>
        </div>
      </n-layout-content>
    </n-layout>
  </div>
</template>

<style scoped>
.app-content {
  min-height: calc(100vh - 57px);
}

.customer-edit-container {
  max-width: 720px;
  margin: 0 auto;
  padding: 24px;
}

.customer-edit-container h2 {
  margin: 0 0 16px;
}

.address-type-label {
  margin: 0 0 8px;
  font-weight: 500;
  color: var(--n-text-color-3, #888);
}
</style>
