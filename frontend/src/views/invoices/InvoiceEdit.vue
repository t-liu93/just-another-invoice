<script setup lang="ts">
import { onMounted, ref, computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import {
  useMessage,
  NButton, NSpace, NInput, NForm, NFormItem, NCard, NSpin, NAlert,
  NDivider, NInputNumber, NSelect, NSwitch, NTag, NDatePicker,
  NGrid, NGi, NText,
} from 'naive-ui'
import { AddOutline, TrashOutline } from '@vicons/ionicons5'
import { NIcon } from 'naive-ui'
import AppHeader from '../../components/AppHeader.vue'
import { useInvoicesStore } from '../../stores/invoices'
import { get } from '../../api/http'
import type { components } from '../../api/schema'

type CustomerRead = components['schemas']['CustomerRead']
type VatRateRead = components['schemas']['VatRateRead']
type VatTreatmentRead = components['schemas']['VatTreatmentRead']
type ProductInvoiceOptionRead = components['schemas']['ProductInvoiceOptionRead']
type InvoiceCalculationRead = components['schemas']['InvoiceCalculationRead']
type InvoiceWrite = components['schemas']['InvoiceWrite']
type InvoiceRead = components['schemas']['InvoiceRead']

interface LineRow {
  product_id: string | null
  name: string
  description: string | null
  quantity: number
  unit_id: string | null
  unit_name: string | null
  unit_price: number
  discount_type: 'NONE' | 'PERCENTAGE' | 'FIXED'
  discount_value: number
  vat_rate_id: string | null
}

function emptyLine(): LineRow {
  return {
    product_id: null,
    name: '',
    description: null,
    quantity: 1,
    unit_id: null,
    unit_name: null,
    unit_price: 0,
    discount_type: 'NONE',
    discount_value: 0,
    vat_rate_id: null,
  }
}

const route = useRoute()
const router = useRouter()
const { t } = useI18n()
const message = useMessage()
const store = useInvoicesStore()

const isEdit = ref(false)
const pageLoading = ref(false)
const saving = ref(false)
const pageError = ref<string | null>(null)
const existingInvoice = ref<InvoiceRead | null>(null)

// Invoice header fields.
const customerId = ref<string | null>(null)
const customers = ref<CustomerRead[]>([])
const selectedCustomer = ref<CustomerRead | null>(null)

const referenceNumber = ref<string | null>(null)
const invoiceDate = ref(new Date().toISOString().slice(0, 10))
const dueDate = ref<string | null>(null)
const taxMode = ref<'LINE' | 'DOCUMENT'>('LINE')
const amountsIncludeVat = ref(false)
const vatTreatmentId = ref<string | null>(null)
const documentVatRateId = ref<string | null>(null)
const discountType = ref<'NONE' | 'PERCENTAGE' | 'FIXED'>('NONE')
const discountValue = ref(0)
const notes = ref<string | null>(null)
const lines = ref<LineRow[]>([emptyLine()])

// Reference data.
const vatRates = ref<VatRateRead[]>([])
const vatTreatments = ref<VatTreatmentRead[]>([])
const productOptions = ref<ProductInvoiceOptionRead[]>([])

// Preview calculation result.
const preview = ref<InvoiceCalculationRead | null>(null)
const previewLoading = ref(false)
const previewError = ref<string | null>(null)

// ------------------------------------------------------------------ fetch ref data

async function loadReferenceData() {
  const [ratesRes, treatsRes] = await Promise.all([
    get<{ items: VatRateRead[] }>('/api/v1/vat-rates'),
    get<{ items: VatTreatmentRead[] }>('/api/v1/vat-treatments?side=SALES'),
  ])
  vatRates.value = ratesRes.items
  vatTreatments.value = treatsRes.items
}

async function searchCustomers(q: string) {
  const res = await get<{ items: CustomerRead[] }>(`/api/v1/customers?q=${encodeURIComponent(q)}&limit=20`)
  customers.value = res.items
}

async function searchProducts(q: string) {
  productOptions.value = await store.fetchProductOptions(q)
}

// ------------------------------------------------------------------ customer selection

const customerOptions = computed(() => customers.value.map(c => ({
  label: c.name,
  value: c.id,
})))

function handleCustomerSelect(id: string | null) {
  customerId.value = id
  selectedCustomer.value = customers.value.find(c => c.id === id) ?? null
  // Auto-derive VAT treatment based on customer
  vatTreatmentId.value = null
}

// ------------------------------------------------------------------ VAT rate options

const vatRateOptions = computed(() => vatRates.value.map(r => ({
  label: `${r.label} (${r.percent}%)`,
  value: r.id,
})))

const vatTreatmentOptions = computed(() => vatTreatments.value
  .filter(t => t.active)
  .map(t => ({ label: `${t.code} – ${t.label}`, value: t.id })))

// ------------------------------------------------------------------ line management

const discountTypeOptions = [
  { label: 'None', value: 'NONE' },
  { label: 'Percentage (%)', value: 'PERCENTAGE' },
  { label: 'Fixed amount', value: 'FIXED' },
]

function addLine() {
  lines.value.push(emptyLine())
}

function removeLine(i: number) {
  if (lines.value.length > 1) {
    lines.value.splice(i, 1)
  }
}

function handleProductSelect(i: number, productId: string | null) {
  const line = lines.value[i]
  line.product_id = productId
  if (productId) {
    const opt = productOptions.value.find(p => p.id === productId)
    if (opt) {
      if (opt.unit_id) line.unit_id = opt.unit_id
      if (opt.unit_name) line.unit_name = opt.unit_name
      if (opt.default_vat_rate_id) line.vat_rate_id = opt.default_vat_rate_id
      if (!line.name) line.name = opt.name
    }
  }
}

// ------------------------------------------------------------------ preview

function buildCalculationRequest() {
  if (!customerId.value) return null
  const linesPayload = lines.value
    .filter(l => l.name.trim())
    .map(l => ({
      product_id: l.product_id ?? undefined,
      name: l.name.trim(),
      description: l.description ?? undefined,
      quantity: String(l.quantity),
      unit_id: l.unit_id ?? undefined,
      unit_name: l.unit_name ?? undefined,
      unit_price: String(l.unit_price),
      discount: { type: l.discount_type, value: String(l.discount_value) },
      vat_rate_id: l.vat_rate_id ?? undefined,
    }))
  if (!linesPayload.length) return null
  return {
    customer_id: customerId.value,
    invoice_date: invoiceDate.value,
    due_date: dueDate.value ?? undefined,
    tax_mode: taxMode.value,
    amounts_include_vat: amountsIncludeVat.value,
    vat_treatment_id: vatTreatmentId.value ?? undefined,
    document_vat_rate_id: documentVatRateId.value ?? undefined,
    discount: { type: discountType.value, value: String(discountValue.value) },
    lines: linesPayload,
  }
}

let previewTimer: ReturnType<typeof setTimeout> | null = null

function schedulePreview() {
  if (previewTimer) clearTimeout(previewTimer)
  previewTimer = setTimeout(async () => {
    const req = buildCalculationRequest()
    if (!req) {
      preview.value = null
      return
    }
    previewLoading.value = true
    previewError.value = null
    try {
      preview.value = await store.calculatePreview(req as Parameters<typeof store.calculatePreview>[0])
    } catch (e: unknown) {
      previewError.value = e instanceof Error ? e.message : String(e)
      preview.value = null
    } finally {
      previewLoading.value = false
    }
  }, 600)
}

watch(
  [customerId, invoiceDate, taxMode, amountsIncludeVat, vatTreatmentId, documentVatRateId,
    discountType, discountValue, lines],
  schedulePreview,
  { deep: true },
)

// ------------------------------------------------------------------ save

function buildWritePayload(): InvoiceWrite | null {
  if (!customerId.value) return null
  const linesPayload = lines.value
    .filter(l => l.name.trim())
    .map(l => ({
      product_id: l.product_id ?? undefined,
      name: l.name.trim(),
      description: l.description ?? undefined,
      quantity: String(l.quantity),
      unit_id: l.unit_id ?? undefined,
      unit_name: l.unit_name ?? undefined,
      unit_price: String(l.unit_price),
      discount: { type: l.discount_type, value: String(l.discount_value) },
      vat_rate_id: l.vat_rate_id ?? undefined,
    }))
  if (!linesPayload.length) return null
  return {
    customer_id: customerId.value,
    reference_number: referenceNumber.value ?? undefined,
    invoice_date: invoiceDate.value,
    due_date: dueDate.value ?? undefined,
    tax_mode: taxMode.value,
    amounts_include_vat: amountsIncludeVat.value,
    vat_treatment_id: vatTreatmentId.value ?? undefined,
    document_vat_rate_id: documentVatRateId.value ?? undefined,
    discount: { type: discountType.value, value: String(discountValue.value) },
    notes: notes.value ?? undefined,
    lines: linesPayload,
  }
}

async function handleSave() {
  const payload = buildWritePayload()
  if (!payload) {
    message.error(t('invoices.validationError'))
    return
  }
  saving.value = true
  try {
    if (isEdit.value && existingInvoice.value) {
      await store.updateInvoice(existingInvoice.value.id, payload)
      message.success(t('invoices.updateSuccess'))
    } else {
      await store.createInvoice(payload)
      message.success(t('invoices.createSuccess'))
    }
    router.push('/invoices')
  } catch (e: unknown) {
    message.error(e instanceof Error ? e.message : t('invoices.saveFailed'))
  } finally {
    saving.value = false
  }
}

async function handleStatusTransition(newStatus: 'SENT' | 'CANCELLED' | 'DRAFT') {
  if (!existingInvoice.value) return
  try {
    await store.transitionStatus(existingInvoice.value.id, { status: newStatus })
    message.success(t('invoices.statusUpdated'))
    router.push('/invoices')
  } catch (e: unknown) {
    message.error(e instanceof Error ? e.message : t('invoices.statusFailed'))
  }
}

// ------------------------------------------------------------------ load existing invoice

function populateFromInvoice(inv: InvoiceRead) {
  existingInvoice.value = inv
  customerId.value = inv.customer_id
  referenceNumber.value = inv.reference_number ?? null
  invoiceDate.value = inv.invoice_date
  dueDate.value = inv.due_date ?? null
  taxMode.value = inv.tax_mode
  amountsIncludeVat.value = inv.amounts_include_vat
  vatTreatmentId.value = inv.vat_treatment_id
  documentVatRateId.value = inv.document_vat_rate_id ?? null
  discountType.value = inv.discount_type
  discountValue.value = Number(inv.discount_value)
  notes.value = inv.notes ?? null
  lines.value = (inv.lines ?? []).map(l => ({
    product_id: l.product_id ?? null,
    name: l.name,
    description: l.description ?? null,
    quantity: Number(l.quantity),
    unit_id: l.unit_id ?? null,
    unit_name: l.unit_name ?? null,
    unit_price: Number(l.unit_price),
    discount_type: l.discount_type as 'NONE' | 'PERCENTAGE' | 'FIXED',
    discount_value: Number(l.discount_value),
    vat_rate_id: l.vat_rate_id ?? null,
  }))
}

onMounted(async () => {
  pageLoading.value = true
  try {
    await Promise.all([
      loadReferenceData(),
      searchCustomers(''),
      store.fetchProductOptions().then(opts => { productOptions.value = opts }),
    ])

    const id = route.params.id as string | undefined
    if (id && id !== 'new') {
      isEdit.value = true
      const inv = await store.fetchInvoice(id)
      populateFromInvoice(inv)
      // Ensure the selected customer is in the list even if not returned by the initial search.
      if (!customers.value.find(c => c.id === inv.customer_id)) {
        const custRes = await get<CustomerRead>(`/api/v1/customers/${inv.customer_id}`)
        customers.value = [custRes, ...customers.value]
      }
    }
  } catch (e: unknown) {
    pageError.value = e instanceof Error ? e.message : String(e)
  } finally {
    pageLoading.value = false
  }
})

const isReadOnly = computed(() => existingInvoice.value?.status !== 'DRAFT' && isEdit.value)

const fmtMoney = (v: string | number) => Number(v).toFixed(2)
</script>

<template>
  <div class="invoice-edit-page">
    <n-layout>
      <AppHeader />

      <n-layout-content class="app-content">
        <n-spin :show="pageLoading">
          <div class="invoice-edit-container">

            <!-- Page title + status actions -->
            <div class="page-header">
              <h2>
                {{ isEdit
                  ? (existingInvoice ? existingInvoice.invoice_number : t('invoices.edit'))
                  : t('invoices.new') }}
              </h2>
              <n-space v-if="existingInvoice" align="center">
                <n-tag :type="existingInvoice.status === 'DRAFT' ? 'default' : existingInvoice.status === 'SENT' ? 'info' : existingInvoice.status === 'COMPLETED' ? 'success' : 'warning'">
                  {{ t(`invoices.status${existingInvoice.status}`) }}
                </n-tag>
                <n-tag :type="existingInvoice.paid_status === 'PAID' ? 'success' : existingInvoice.paid_status === 'PARTIALLY_PAID' ? 'warning' : 'default'">
                  {{ t(`invoices.paidStatus${existingInvoice.paid_status}`) }}
                </n-tag>
                <n-text depth="3" style="font-size: 13px">
                  {{ t('invoices.due') }}: {{ existingInvoice.currency }} {{ fmtMoney(existingInvoice.due_amount) }}
                </n-text>
                <template v-if="existingInvoice.status === 'DRAFT'">
                  <n-button size="small" type="info" @click="handleStatusTransition('SENT')">
                    {{ t('invoices.markSent') }}
                  </n-button>
                  <n-button size="small" type="warning" @click="handleStatusTransition('CANCELLED')">
                    {{ t('invoices.cancel') }}
                  </n-button>
                </template>
                <template v-else-if="existingInvoice.status === 'CANCELLED'">
                  <n-button size="small" type="primary" @click="handleStatusTransition('DRAFT')">
                    {{ t('invoices.reactivate') }}
                  </n-button>
                </template>
              </n-space>
            </div>

            <n-alert v-if="pageError" type="error" style="margin-bottom: 16px">
              {{ pageError }}
            </n-alert>

            <n-form label-placement="top" :disabled="isReadOnly">

              <!-- Invoice header -->
              <n-card :title="t('invoices.headerSection')" style="margin-bottom: 16px">
                <n-grid :cols="2" :x-gap="16" :y-gap="0">
                  <n-gi>
                    <n-form-item :label="t('invoices.customer')" required>
                      <n-select
                        v-model:value="customerId"
                        filterable
                        clearable
                        remote
                        :options="customerOptions"
                        :placeholder="t('invoices.customerSearch')"
                        @search="searchCustomers"
                        @update:value="handleCustomerSelect"
                      />
                    </n-form-item>
                  </n-gi>
                  <n-gi>
                    <n-form-item :label="t('invoices.referenceNumber')">
                      <n-input v-model:value="referenceNumber" :placeholder="t('invoices.referenceNumberPlaceholder')" clearable />
                    </n-form-item>
                  </n-gi>
                  <n-gi>
                    <n-form-item :label="t('invoices.invoiceDate')" required>
                      <n-date-picker
                        v-model:formatted-value="invoiceDate"
                        value-format="yyyy-MM-dd"
                        type="date"
                        clearable
                        style="width: 100%"
                      />
                    </n-form-item>
                  </n-gi>
                  <n-gi>
                    <n-form-item :label="t('invoices.dueDate')">
                      <n-date-picker
                        v-model:formatted-value="dueDate"
                        value-format="yyyy-MM-dd"
                        type="date"
                        clearable
                        style="width: 100%"
                      />
                    </n-form-item>
                  </n-gi>
                </n-grid>
              </n-card>

              <!-- VAT settings -->
              <n-card :title="t('invoices.vatSection')" style="margin-bottom: 16px">
                <n-grid :cols="2" :x-gap="16" :y-gap="0">
                  <n-gi>
                    <n-form-item :label="t('invoices.taxMode')">
                      <n-select
                        v-model:value="taxMode"
                        :options="[
                          { label: t('invoices.taxModeLine'), value: 'LINE' },
                          { label: t('invoices.taxModeDocument'), value: 'DOCUMENT' },
                        ]"
                      />
                    </n-form-item>
                  </n-gi>
                  <n-gi>
                    <n-form-item :label="t('invoices.amountsIncludeVat')">
                      <n-switch v-model:value="amountsIncludeVat" />
                    </n-form-item>
                  </n-gi>
                  <n-gi>
                    <n-form-item :label="t('invoices.vatTreatment')">
                      <n-select
                        v-model:value="vatTreatmentId"
                        :options="vatTreatmentOptions"
                        :placeholder="t('invoices.vatTreatmentAuto')"
                        clearable
                      />
                    </n-form-item>
                  </n-gi>
                  <n-gi v-if="taxMode === 'DOCUMENT'">
                    <n-form-item :label="t('invoices.documentVatRate')" required>
                      <n-select
                        v-model:value="documentVatRateId"
                        :options="vatRateOptions"
                        :placeholder="t('invoices.selectVatRate')"
                        clearable
                      />
                    </n-form-item>
                  </n-gi>
                </n-grid>
              </n-card>

              <!-- Discount & notes -->
              <n-card :title="t('invoices.discountSection')" style="margin-bottom: 16px">
                <n-grid :cols="3" :x-gap="16" :y-gap="0">
                  <n-gi>
                    <n-form-item :label="t('invoices.discountType')">
                      <n-select v-model:value="discountType" :options="discountTypeOptions" />
                    </n-form-item>
                  </n-gi>
                  <n-gi v-if="discountType !== 'NONE'">
                    <n-form-item :label="discountType === 'PERCENTAGE' ? t('invoices.discountPercent') : t('invoices.discountFixed')">
                      <n-input-number v-model:value="discountValue" :min="0" :precision="3" />
                    </n-form-item>
                  </n-gi>
                  <n-gi :span="3">
                    <n-form-item :label="t('invoices.notes')">
                      <n-input v-model:value="notes" type="textarea" :autosize="{ minRows: 2 }" clearable />
                    </n-form-item>
                  </n-gi>
                </n-grid>
              </n-card>

              <!-- Line items -->
              <n-card :title="t('invoices.linesSection')" style="margin-bottom: 16px">
                <div v-for="(line, i) in lines" :key="i" class="line-row">
                  <n-divider v-if="i > 0" />

                  <n-grid :cols="12" :x-gap="8" :y-gap="4">
                    <!-- Product selector -->
                    <n-gi :span="4">
                      <n-form-item :label="t('invoices.product')" size="small">
                        <n-select
                          :value="line.product_id"
                          filterable
                          clearable
                          remote
                          :options="productOptions.map(p => ({ label: p.name, value: p.id }))"
                          :placeholder="t('invoices.productSearch')"
                          @search="searchProducts"
                          @update:value="(v: string | null) => handleProductSelect(i, v)"
                        />
                      </n-form-item>
                    </n-gi>

                    <!-- Name -->
                    <n-gi :span="5">
                      <n-form-item :label="t('invoices.lineName')" size="small" required>
                        <n-input v-model:value="line.name" :placeholder="t('invoices.lineNamePlaceholder')" />
                      </n-form-item>
                    </n-gi>

                    <!-- Remove button -->
                    <n-gi :span="1" style="display: flex; align-items: flex-end; padding-bottom: 2px">
                      <n-button
                        v-if="lines.length > 1 && !isReadOnly"
                        size="small"
                        quaternary
                        circle
                        type="error"
                        @click="removeLine(i)"
                      >
                        <template #icon><n-icon><TrashOutline /></n-icon></template>
                      </n-button>
                    </n-gi>

                    <!-- Description -->
                    <n-gi :span="12">
                      <n-form-item :label="t('invoices.lineDescription')" size="small">
                        <n-input v-model:value="line.description" :placeholder="t('invoices.lineDescriptionPlaceholder')" clearable />
                      </n-form-item>
                    </n-gi>

                    <!-- Qty -->
                    <n-gi :span="2">
                      <n-form-item :label="t('invoices.quantity')" size="small">
                        <n-input-number v-model:value="line.quantity" :min="0.001" :precision="3" />
                      </n-form-item>
                    </n-gi>

                    <!-- Unit name -->
                    <n-gi :span="2">
                      <n-form-item :label="t('invoices.unit')" size="small">
                        <n-input v-model:value="line.unit_name" :placeholder="t('invoices.unitPlaceholder')" clearable />
                      </n-form-item>
                    </n-gi>

                    <!-- Unit price -->
                    <n-gi :span="3">
                      <n-form-item :label="amountsIncludeVat ? t('invoices.unitPriceIncl') : t('invoices.unitPriceExcl')" size="small">
                        <n-input-number v-model:value="line.unit_price" :min="0" :precision="3" />
                      </n-form-item>
                    </n-gi>

                    <!-- Discount type -->
                    <n-gi :span="2">
                      <n-form-item :label="t('invoices.lineDiscountType')" size="small">
                        <n-select v-model:value="line.discount_type" :options="discountTypeOptions" size="small" />
                      </n-form-item>
                    </n-gi>

                    <!-- Discount value -->
                    <n-gi v-if="line.discount_type !== 'NONE'" :span="2">
                      <n-form-item :label="t('invoices.lineDiscountValue')" size="small">
                        <n-input-number v-model:value="line.discount_value" :min="0" :precision="3" size="small" />
                      </n-form-item>
                    </n-gi>

                    <!-- VAT rate (LINE mode only) -->
                    <n-gi v-if="taxMode === 'LINE'" :span="3">
                      <n-form-item :label="t('invoices.vatRate')" size="small">
                        <n-select
                          v-model:value="line.vat_rate_id"
                          :options="vatRateOptions"
                          :placeholder="t('invoices.selectVatRate')"
                          clearable
                          size="small"
                        />
                      </n-form-item>
                    </n-gi>
                  </n-grid>
                </div>

                <n-button
                  v-if="!isReadOnly"
                  dashed
                  block
                  style="margin-top: 12px"
                  @click="addLine"
                >
                  <template #icon><n-icon><AddOutline /></n-icon></template>
                  {{ t('invoices.addLine') }}
                </n-button>
              </n-card>

              <!-- Preview totals -->
              <n-card :title="t('invoices.totalsSection')" style="margin-bottom: 24px">
                <div v-if="previewLoading" style="text-align:center; padding: 12px">
                  <n-spin />
                </div>
                <n-alert v-else-if="previewError" type="error">{{ previewError }}</n-alert>
                <div v-else-if="preview" class="totals-table">
                  <div class="total-row">
                    <span>{{ t('invoices.subtotalExclVat') }}</span>
                    <n-text>{{ fmtMoney(preview.subtotal_excl_vat) }} </n-text>
                  </div>
                  <div v-if="Number(preview.line_discount_total) !== 0" class="total-row">
                    <span>{{ t('invoices.lineDiscountTotal') }}</span>
                    <n-text>-{{ fmtMoney(preview.line_discount_total) }}</n-text>
                  </div>
                  <div v-if="Number(preview.document_discount_amount) !== 0" class="total-row">
                    <span>{{ t('invoices.documentDiscountAmount') }}</span>
                    <n-text>-{{ fmtMoney(preview.document_discount_amount) }}</n-text>
                  </div>
                  <div class="total-row">
                    <span>{{ t('invoices.taxableAmount') }}</span>
                    <n-text>{{ fmtMoney(preview.taxable_amount) }}</n-text>
                  </div>
                  <div class="total-row">
                    <span>{{ t('invoices.vatTotal') }}</span>
                    <n-text>{{ fmtMoney(preview.vat_total) }}</n-text>
                  </div>
                  <n-divider style="margin: 8px 0" />
                  <div class="total-row total-row--bold">
                    <span>{{ t('invoices.totalInclVat') }}</span>
                    <n-text strong>{{ fmtMoney(preview.total_incl_vat) }}</n-text>
                  </div>
                  <div v-if="preview.vat_treatment_snapshot" class="total-row" style="font-size: 12px; color: var(--n-text-color-3)">
                    <span>{{ t('invoices.vatTreatmentApplied') }}</span>
                    <span>{{ preview.vat_treatment_snapshot.code }}</span>
                  </div>
                </div>
                <n-text v-else depth="3">{{ t('invoices.totalsPlaceholder') }}</n-text>
              </n-card>

            </n-form>

            <!-- Action buttons -->
            <n-space justify="end" style="margin-bottom: 24px">
              <n-button @click="router.push('/invoices')">{{ t('invoices.backToList') }}</n-button>
              <n-button
                v-if="!isReadOnly"
                type="primary"
                :loading="saving"
                @click="handleSave"
              >
                {{ isEdit ? t('invoices.save') : t('invoices.create') }}
              </n-button>
            </n-space>

          </div>
        </n-spin>
      </n-layout-content>
    </n-layout>
  </div>
</template>

<style scoped>
.app-content {
  min-height: calc(100vh - 57px);
}

.invoice-edit-container {
  max-width: 1080px;
  margin: 0 auto;
  padding: 24px;
}

.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 20px;
  gap: 12px;
}

.page-header h2 {
  margin: 0;
}

.totals-table {
  max-width: 380px;
  margin-left: auto;
}

.total-row {
  display: flex;
  justify-content: space-between;
  padding: 4px 0;
}

.total-row--bold {
  font-weight: 600;
  font-size: 16px;
}

.line-row {
  margin-bottom: 8px;
}
</style>
