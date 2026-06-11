<script setup lang="ts">
import { onMounted, ref, computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import {
  useMessage, useDialog,
  NButton, NSpace, NInput, NForm, NFormItem, NCard, NSpin, NAlert,
  NDivider, NInputNumber, NSelect, NSwitch, NTag, NDatePicker,
  NGrid, NGi, NText, NModal, NList, NListItem, NThing,
} from 'naive-ui'
import { AddOutline, TrashOutline, DocumentTextOutline } from '@vicons/ionicons5'
import { NIcon } from 'naive-ui'
import AppHeader from '../../components/AppHeader.vue'
import { useQuotesStore } from '../../stores/quotes'
import { useInvoicesStore } from '../../stores/invoices'
import { get } from '../../api/http'
import type { components } from '../../api/schema'

type CustomerRead = components['schemas']['CustomerRead']
type VatRateRead = components['schemas']['VatRateRead']
type VatTreatmentRead = components['schemas']['VatTreatmentRead']
type ProductInvoiceOptionRead = components['schemas']['ProductInvoiceOptionRead']
type QuoteCalculationRead = components['schemas']['QuoteCalculationRead']
type QuoteWrite = components['schemas']['QuoteWrite']
type QuoteRead = components['schemas']['QuoteRead']
type DocumentTemplateRead = components['schemas']['DocumentTemplateRead']
type ContentBlockRead = components['schemas']['ContentBlockRead']
type NoteTemplateRead = components['schemas']['NoteTemplateRead']

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
const dialog = useDialog()
const store = useQuotesStore()
const invoicesStore = useInvoicesStore()

const isEdit = ref(false)
const pageLoading = ref(false)
const saving = ref(false)
const pageError = ref<string | null>(null)
const existingQuote = ref<QuoteRead | null>(null)

// Quote header fields
const customerId = ref<string | null>(null)
const customers = ref<CustomerRead[]>([])
const referenceNumber = ref<string | null>(null)
const quoteDate = ref(new Date().toISOString().slice(0, 10))
const validUntil = ref<string | null>(null)
const taxMode = ref<'LINE' | 'DOCUMENT'>('LINE')
const amountsIncludeVat = ref(false)
const vatTreatmentId = ref<string | null>(null)
const documentVatRateId = ref<string | null>(null)
const discountType = ref<'NONE' | 'PERCENTAGE' | 'FIXED'>('NONE')
const discountValue = ref(0)

// Content fields
const notes = ref<string | null>(null)
const warrantyText = ref<string | null>(null)
const termsText = ref<string | null>(null)
const bankText = ref<string | null>(null)
const paymentTermsText = ref<string | null>(null)

const lines = ref<LineRow[]>([emptyLine()])

// Reference data
const vatRates = ref<VatRateRead[]>([])
const vatTreatments = ref<VatTreatmentRead[]>([])
const productOptions = ref<ProductInvoiceOptionRead[]>([])

// Preview
const preview = ref<QuoteCalculationRead | null>(null)
const previewLoading = ref(false)
const previewError = ref<string | null>(null)

// Content libraries
const documentTemplates = ref<DocumentTemplateRead[]>([])
const contentBlocks = ref<ContentBlockRead[]>([])
const noteTemplates = ref<NoteTemplateRead[]>([])
const showTemplateModal = ref(false)
const showNoteTemplateModal = ref(false)

// Dirty tracking + reactivate modal
const cleanSnapshot = ref<string | null>(null)
const showReactivateModal = ref(false)
const reactivateDate = ref<string | null>(null)
const reactivating = ref(false)

// ------------------------------------------------------------------ fetch ref data

async function loadReferenceData() {
  const [ratesRes, treatsRes] = await Promise.all([
    get<{ items: VatRateRead[] }>('/api/v1/vat-rates'),
    get<{ items: VatTreatmentRead[] }>('/api/v1/vat-treatments?side=SALES'),
  ])
  vatRates.value = ratesRes.items
  vatTreatments.value = treatsRes.items
}

async function loadContentLibraries() {
  const [templates, blocks, noteTpls] = await Promise.all([
    get<DocumentTemplateRead[]>('/api/v1/document-templates'),
    get<ContentBlockRead[]>('/api/v1/content-blocks'),
    get<NoteTemplateRead[]>('/api/v1/note-templates'),
  ])
  documentTemplates.value = templates
  contentBlocks.value = blocks
  noteTemplates.value = noteTpls
}

async function searchCustomers(q: string) {
  const res = await get<{ items: CustomerRead[] }>(`/api/v1/customers?q=${encodeURIComponent(q)}&limit=20`)
  customers.value = res.items
}

async function searchProducts(q: string) {
  productOptions.value = await invoicesStore.fetchProductOptions(q)
}

// ------------------------------------------------------------------ customer selection

const customerOptions = computed(() => customers.value.map(c => ({
  label: c.name,
  value: c.id,
})))

function handleCustomerSelect(id: string | null) {
  customerId.value = id
  vatTreatmentId.value = null
}

// ------------------------------------------------------------------ VAT options

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
  if (lines.value.length > 1) lines.value.splice(i, 1)
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

// ------------------------------------------------------------------ document template apply

const filteredTemplates = computed(() =>
  documentTemplates.value.filter(t => t.applies_to === 'QUOTE' || t.applies_to === 'BOTH')
)

function applyTemplate(template: DocumentTemplateRead) {
  lines.value = (template.lines ?? []).map(l => ({
    product_id: null,
    name: l.name,
    description: l.description ?? null,
    quantity: Number(l.quantity),
    unit_id: l.unit_id ?? null,
    unit_name: l.unit_name ?? null,
    unit_price: l.unit_price != null ? Number(l.unit_price) : 0,
    discount_type: (l.discount_type as 'NONE' | 'PERCENTAGE' | 'FIXED') ?? 'NONE',
    discount_value: Number(l.discount_value),
    vat_rate_id: l.vat_rate_id ?? null,
  }))
  showTemplateModal.value = false
}

// ------------------------------------------------------------------ note template insert

function insertNoteTemplate(body: string) {
  notes.value = notes.value ? `${notes.value}\n${body}` : body
  showNoteTemplateModal.value = false
}

// ------------------------------------------------------------------ content blocks preload

function applyDefaultContentBlocks() {
  for (const kind of ['WARRANTY', 'TERMS', 'BANK', 'PAYMENT_TERMS'] as const) {
    const defaultBlock = contentBlocks.value.find(b => b.kind === kind && b.is_default)
    if (defaultBlock) {
      if (kind === 'WARRANTY' && !warrantyText.value) warrantyText.value = defaultBlock.body
      if (kind === 'TERMS' && !termsText.value) termsText.value = defaultBlock.body
      if (kind === 'BANK' && !bankText.value) bankText.value = defaultBlock.body
      if (kind === 'PAYMENT_TERMS' && !paymentTermsText.value) paymentTermsText.value = defaultBlock.body
    }
  }
}

const contentBlocksByKind = computed(() => {
  const result: Record<string, ContentBlockRead[]> = {}
  for (const b of contentBlocks.value) {
    if (!result[b.kind]) result[b.kind] = []
    result[b.kind].push(b)
  }
  return result
})

function selectContentBlock(kind: 'WARRANTY' | 'TERMS' | 'BANK' | 'PAYMENT_TERMS', id: string) {
  const block = contentBlocks.value.find(b => b.id === id)
  if (!block) return
  if (kind === 'WARRANTY') warrantyText.value = block.body
  if (kind === 'TERMS') termsText.value = block.body
  if (kind === 'BANK') bankText.value = block.body
  if (kind === 'PAYMENT_TERMS') paymentTermsText.value = block.body
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
    quote_date: quoteDate.value,
    valid_until: validUntil.value ?? undefined,
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
    if (!req) { preview.value = null; return }
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
  [customerId, quoteDate, taxMode, amountsIncludeVat, vatTreatmentId, documentVatRateId,
    discountType, discountValue, lines],
  schedulePreview,
  { deep: true },
)

function currentFormState(): string {
  return JSON.stringify({
    customerId: customerId.value,
    referenceNumber: referenceNumber.value,
    quoteDate: quoteDate.value,
    validUntil: validUntil.value,
    taxMode: taxMode.value,
    amountsIncludeVat: amountsIncludeVat.value,
    vatTreatmentId: vatTreatmentId.value,
    documentVatRateId: documentVatRateId.value,
    discountType: discountType.value,
    discountValue: discountValue.value,
    notes: notes.value,
    warrantyText: warrantyText.value,
    termsText: termsText.value,
    bankText: bankText.value,
    paymentTermsText: paymentTermsText.value,
    lines: lines.value,
  })
}

const formDirty = computed(() => {
  if (!isEdit.value || !existingQuote.value || cleanSnapshot.value === null) return false
  return currentFormState() !== cleanSnapshot.value
})

function isDateDisabled(ts: number): boolean {
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  return ts < today.getTime()
}

// ------------------------------------------------------------------ save

function buildWritePayload(): QuoteWrite | null {
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
    quote_date: quoteDate.value,
    valid_until: validUntil.value ?? undefined,
    tax_mode: taxMode.value,
    amounts_include_vat: amountsIncludeVat.value,
    vat_treatment_id: vatTreatmentId.value ?? undefined,
    document_vat_rate_id: documentVatRateId.value ?? undefined,
    discount: { type: discountType.value, value: String(discountValue.value) },
    notes: notes.value ?? undefined,
    warranty_text: warrantyText.value ?? undefined,
    terms_text: termsText.value ?? undefined,
    bank_text: bankText.value ?? undefined,
    payment_terms_text: paymentTermsText.value ?? undefined,
    lines: linesPayload,
  }
}

async function handleSave() {
  const payload = buildWritePayload()
  if (!payload) {
    message.error(t('quotes.validationError'))
    return
  }
  saving.value = true
  try {
    if (isEdit.value && existingQuote.value) {
      await store.updateQuote(existingQuote.value.id, payload)
      message.success(t('quotes.updateSuccess'))
    } else {
      await store.createQuote(payload)
      message.success(t('quotes.createSuccess'))
    }
    router.push('/quotes')
  } catch (e: unknown) {
    message.error(e instanceof Error ? e.message : t('quotes.saveFailed'))
  } finally {
    saving.value = false
  }
}

// ------------------------------------------------------------------ status actions

async function withDirtyCheck(): Promise<boolean> {
  if (!formDirty.value) return true
  return new Promise<boolean>((resolve) => {
    dialog.warning({
      title: t('quotes.unsavedChanges'),
      content: t('quotes.unsavedChangesContent'),
      positiveText: t('quotes.saveAndContinue'),
      negativeText: t('quotes.cancel'),
      onPositiveClick: async () => {
        if (!existingQuote.value) { resolve(false); return }
        const payload = buildWritePayload()
        if (!payload) {
          message.error(t('quotes.validationError'))
          resolve(false)
          return
        }
        saving.value = true
        try {
          await store.updateQuote(existingQuote.value.id, payload)
          cleanSnapshot.value = currentFormState()
          resolve(true)
        } catch (e: unknown) {
          message.error(e instanceof Error ? e.message : t('quotes.saveFailed'))
          resolve(false)
        } finally {
          saving.value = false
        }
      },
      onNegativeClick: () => resolve(false),
      onClose: () => resolve(false),
    })
  })
}

async function handleStatusTransition(newStatus: 'SENT' | 'ACCEPTED' | 'REJECTED' | 'DRAFT') {
  if (!existingQuote.value) return
  if (!(await withDirtyCheck())) return
  try {
    await store.transitionStatus(existingQuote.value.id, { status: newStatus })
    message.success(t('quotes.statusUpdated'))
    router.push('/quotes')
  } catch (e: unknown) {
    message.error(e instanceof Error ? e.message : t('quotes.statusFailed'))
  }
}

async function handleConvert() {
  if (!existingQuote.value) return
  if (!(await withDirtyCheck())) return
  try {
    const invoice = await store.convertQuote(existingQuote.value.id)
    message.success(t('quotes.convertSuccess'))
    router.push(`/invoices/${invoice.id}/edit`)
  } catch (e: unknown) {
    message.error(e instanceof Error ? e.message : t('quotes.convertFailed'))
  }
}

function handleReactivate() {
  if (!existingQuote.value) return
  reactivateDate.value = null
  showReactivateModal.value = true
}

async function confirmReactivate() {
  if (!existingQuote.value) return
  if (!(await withDirtyCheck())) return
  reactivating.value = true
  try {
    const payload = reactivateDate.value ? { valid_until: reactivateDate.value } : {}
    await store.reactivateQuote(existingQuote.value.id, payload)
    message.success(t('quotes.reactivateSuccess'))
    showReactivateModal.value = false
    router.push('/quotes')
  } catch (e: unknown) {
    message.error(e instanceof Error ? e.message : t('quotes.reactivateFailed'))
  } finally {
    reactivating.value = false
  }
}

// ------------------------------------------------------------------ load existing quote

function populateFromQuote(q: QuoteRead) {
  existingQuote.value = q
  customerId.value = q.customer_id
  referenceNumber.value = q.reference_number ?? null
  quoteDate.value = q.quote_date
  validUntil.value = q.valid_until ?? null
  taxMode.value = q.tax_mode
  amountsIncludeVat.value = q.amounts_include_vat
  vatTreatmentId.value = q.vat_treatment_id
  documentVatRateId.value = q.document_vat_rate_id ?? null
  discountType.value = q.discount_type
  discountValue.value = Number(q.discount_value)
  notes.value = q.notes ?? null
  warrantyText.value = q.warranty_text ?? null
  termsText.value = q.terms_text ?? null
  bankText.value = q.bank_text ?? null
  paymentTermsText.value = q.payment_terms_text ?? null
  lines.value = (q.lines ?? []).map(l => ({
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
  cleanSnapshot.value = currentFormState()
}

onMounted(async () => {
  pageLoading.value = true
  try {
    await Promise.all([
      loadReferenceData(),
      loadContentLibraries(),
      searchCustomers(''),
      invoicesStore.fetchProductOptions().then(opts => { productOptions.value = opts }),
    ])

    const id = route.params.id as string | undefined
    if (id && id !== 'new') {
      isEdit.value = true
      const q = await store.fetchQuote(id)
      populateFromQuote(q)
      if (!customers.value.find(c => c.id === q.customer_id)) {
        const custRes = await get<CustomerRead>(`/api/v1/customers/${q.customer_id}`)
        customers.value = [custRes, ...customers.value]
      }
    } else {
      // New quote – apply default content blocks
      applyDefaultContentBlocks()
    }
  } catch (e: unknown) {
    pageError.value = e instanceof Error ? e.message : String(e)
  } finally {
    pageLoading.value = false
  }
})

// ACCEPTED is the only read-only state; expired/rejected can still be edited
const isReadOnly = computed(() => existingQuote.value?.status === 'ACCEPTED' && isEdit.value)

const fmtMoney = (v: string | number) => Number(v).toFixed(2)
</script>

<template>
  <div class="quote-edit-page">
    <n-layout>
      <AppHeader />

      <n-layout-content class="app-content">
        <n-spin :show="pageLoading">
          <div class="quote-edit-container">

            <!-- Page title + status actions -->
            <div class="page-header">
              <h2>
                {{ isEdit
                  ? (existingQuote ? existingQuote.quote_number : t('quotes.edit'))
                  : t('quotes.new') }}
              </h2>
              <n-space v-if="existingQuote" align="center">
                <n-tag :type="existingQuote.status === 'DRAFT' ? 'default'
                  : existingQuote.status === 'SENT' ? 'info'
                  : existingQuote.status === 'ACCEPTED' ? 'success'
                  : existingQuote.status === 'REJECTED' ? 'error'
                  : 'warning'">
                  {{ t(`quotes.status${existingQuote.status}`) }}
                </n-tag>

                <!-- Show link to converted invoice -->
                <n-text v-if="existingQuote.converted_invoice_id" depth="3" style="font-size: 13px">
                  {{ t('quotes.convertedInvoiceLink') }}:
                  <a
                    style="cursor: pointer; color: var(--n-color-target)"
                    @click="router.push(`/invoices/${existingQuote!.converted_invoice_id}/edit`)"
                  >{{ t('quotes.viewInvoice') }}</a>
                </n-text>

                <!-- DRAFT actions -->
                <template v-if="existingQuote.status === 'DRAFT'">
                  <n-button size="small" type="info" @click="handleStatusTransition('SENT')">
                    {{ t('quotes.markSent') }}
                  </n-button>
                </template>

                <!-- SENT actions -->
                <template v-else-if="existingQuote.status === 'SENT'">
                  <n-button size="small" type="success" @click="handleStatusTransition('ACCEPTED')">
                    {{ t('quotes.markAccepted') }}
                  </n-button>
                  <n-button size="small" type="error" @click="handleStatusTransition('REJECTED')">
                    {{ t('quotes.markRejected') }}
                  </n-button>
                  <n-button size="small" type="primary" @click="handleConvert">
                    {{ t('quotes.convertToInvoice') }}
                  </n-button>
                </template>

                <!-- REJECTED actions -->
                <template v-else-if="existingQuote.status === 'REJECTED'">
                  <n-button size="small" type="info" @click="handleStatusTransition('SENT')">
                    {{ t('quotes.reSend') }}
                  </n-button>
                </template>

                <!-- EXPIRED actions -->
                <template v-else-if="existingQuote.status === 'EXPIRED'">
                  <n-button size="small" type="primary" @click="handleConvert">
                    {{ t('quotes.convertToInvoice') }}
                  </n-button>
                  <n-button size="small" type="warning" @click="handleReactivate">
                    {{ t('quotes.reactivate') }}
                  </n-button>
                </template>

                <!-- ACCEPTED actions -->
                <template v-else-if="existingQuote.status === 'ACCEPTED'">
                  <n-button
                    v-if="!existingQuote.converted_invoice_id"
                    size="small"
                    type="primary"
                    @click="handleConvert"
                  >
                    {{ t('quotes.convertToInvoice') }}
                  </n-button>
                </template>
              </n-space>
            </div>

            <n-alert v-if="pageError" type="error" style="margin-bottom: 16px">
              {{ pageError }}
            </n-alert>

            <n-form label-placement="top" :disabled="isReadOnly">

              <!-- Quote header -->
              <n-card :title="t('quotes.headerSection')" style="margin-bottom: 16px">
                <n-grid :cols="2" :x-gap="16" :y-gap="0">
                  <n-gi>
                    <n-form-item :label="t('quotes.customer')" required>
                      <n-select
                        v-model:value="customerId"
                        filterable
                        clearable
                        remote
                        :options="customerOptions"
                        :placeholder="t('quotes.customerSearch')"
                        @search="searchCustomers"
                        @update:value="handleCustomerSelect"
                      />
                    </n-form-item>
                  </n-gi>
                  <n-gi>
                    <n-form-item :label="t('quotes.referenceNumber')">
                      <n-input v-model:value="referenceNumber" :placeholder="t('quotes.referenceNumberPlaceholder')" clearable />
                    </n-form-item>
                  </n-gi>
                  <n-gi>
                    <n-form-item :label="t('quotes.quoteDate')" required>
                      <n-date-picker
                        v-model:formatted-value="quoteDate"
                        value-format="yyyy-MM-dd"
                        type="date"
                        clearable
                        style="width: 100%"
                      />
                    </n-form-item>
                  </n-gi>
                  <n-gi>
                    <n-form-item :label="t('quotes.validUntil')">
                      <n-date-picker
                        v-model:formatted-value="validUntil"
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
              <n-card :title="t('quotes.vatSection')" style="margin-bottom: 16px">
                <n-grid :cols="2" :x-gap="16" :y-gap="0">
                  <n-gi>
                    <n-form-item :label="t('quotes.taxMode')">
                      <n-select
                        v-model:value="taxMode"
                        :options="[
                          { label: t('quotes.taxModeLine'), value: 'LINE' },
                          { label: t('quotes.taxModeDocument'), value: 'DOCUMENT' },
                        ]"
                      />
                    </n-form-item>
                  </n-gi>
                  <n-gi>
                    <n-form-item :label="t('quotes.amountsIncludeVat')">
                      <n-switch v-model:value="amountsIncludeVat" />
                    </n-form-item>
                  </n-gi>
                  <n-gi>
                    <n-form-item :label="t('quotes.vatTreatment')">
                      <n-select
                        v-model:value="vatTreatmentId"
                        :options="vatTreatmentOptions"
                        :placeholder="t('quotes.vatTreatmentAuto')"
                        clearable
                      />
                    </n-form-item>
                  </n-gi>
                  <n-gi v-if="taxMode === 'DOCUMENT'">
                    <n-form-item :label="t('quotes.documentVatRate')" required>
                      <n-select
                        v-model:value="documentVatRateId"
                        :options="vatRateOptions"
                        :placeholder="t('quotes.selectVatRate')"
                        clearable
                      />
                    </n-form-item>
                  </n-gi>
                </n-grid>
              </n-card>

              <!-- Discount -->
              <n-card :title="t('quotes.discountSection')" style="margin-bottom: 16px">
                <n-grid :cols="3" :x-gap="16" :y-gap="0">
                  <n-gi>
                    <n-form-item :label="t('quotes.discountType')">
                      <n-select v-model:value="discountType" :options="discountTypeOptions" />
                    </n-form-item>
                  </n-gi>
                  <n-gi v-if="discountType !== 'NONE'">
                    <n-form-item :label="discountType === 'PERCENTAGE' ? t('quotes.discountPercent') : t('quotes.discountFixed')">
                      <n-input-number v-model:value="discountValue" :min="0" :precision="3" />
                    </n-form-item>
                  </n-gi>
                </n-grid>
              </n-card>

              <!-- Content blocks + Notes -->
              <n-card :title="t('quotes.contentSection')" style="margin-bottom: 16px">
                <!-- Notes -->
                <n-form-item :label="t('quotes.notes')">
                  <div style="width: 100%">
                    <n-input
                      v-model:value="notes"
                      type="textarea"
                      :autosize="{ minRows: 2 }"
                      clearable
                    />
                    <n-button
                      v-if="!isReadOnly && noteTemplates.length > 0"
                      size="small"
                      secondary
                      style="margin-top: 4px"
                      @click="showNoteTemplateModal = true"
                    >
                      {{ t('quotes.insertNoteTemplate') }}
                    </n-button>
                  </div>
                </n-form-item>

                <!-- Warranty -->
                <n-form-item :label="t('quotes.warrantyText')">
                  <div style="width: 100%">
                    <div v-if="!isReadOnly && contentBlocksByKind['WARRANTY']?.length" style="margin-bottom: 4px">
                      <n-select
                        :options="(contentBlocksByKind['WARRANTY'] ?? []).map(b => ({ label: b.name, value: b.id }))"
                        :placeholder="t('quotes.selectContentBlock')"
                        size="small"
                        style="width: 250px"
                        clearable
                        @update:value="(v: string) => selectContentBlock('WARRANTY', v)"
                      />
                    </div>
                    <n-input v-model:value="warrantyText" type="textarea" :autosize="{ minRows: 2 }" clearable />
                  </div>
                </n-form-item>

                <!-- Terms -->
                <n-form-item :label="t('quotes.termsText')">
                  <div style="width: 100%">
                    <div v-if="!isReadOnly && contentBlocksByKind['TERMS']?.length" style="margin-bottom: 4px">
                      <n-select
                        :options="(contentBlocksByKind['TERMS'] ?? []).map(b => ({ label: b.name, value: b.id }))"
                        :placeholder="t('quotes.selectContentBlock')"
                        size="small"
                        style="width: 250px"
                        clearable
                        @update:value="(v: string) => selectContentBlock('TERMS', v)"
                      />
                    </div>
                    <n-input v-model:value="termsText" type="textarea" :autosize="{ minRows: 2 }" clearable />
                  </div>
                </n-form-item>

                <!-- Bank info -->
                <n-form-item :label="t('quotes.bankText')">
                  <div style="width: 100%">
                    <div v-if="!isReadOnly && contentBlocksByKind['BANK']?.length" style="margin-bottom: 4px">
                      <n-select
                        :options="(contentBlocksByKind['BANK'] ?? []).map(b => ({ label: b.name, value: b.id }))"
                        :placeholder="t('quotes.selectContentBlock')"
                        size="small"
                        style="width: 250px"
                        clearable
                        @update:value="(v: string) => selectContentBlock('BANK', v)"
                      />
                    </div>
                    <n-input v-model:value="bankText" type="textarea" :autosize="{ minRows: 2 }" clearable />
                  </div>
                </n-form-item>

                <!-- Payment terms -->
                <n-form-item :label="t('quotes.paymentTermsText')">
                  <div style="width: 100%">
                    <div v-if="!isReadOnly && contentBlocksByKind['PAYMENT_TERMS']?.length" style="margin-bottom: 4px">
                      <n-select
                        :options="(contentBlocksByKind['PAYMENT_TERMS'] ?? []).map(b => ({ label: b.name, value: b.id }))"
                        :placeholder="t('quotes.selectContentBlock')"
                        size="small"
                        style="width: 250px"
                        clearable
                        @update:value="(v: string) => selectContentBlock('PAYMENT_TERMS', v)"
                      />
                    </div>
                    <n-input v-model:value="paymentTermsText" type="textarea" :autosize="{ minRows: 2 }" clearable />
                  </div>
                </n-form-item>
              </n-card>

              <!-- Line items -->
              <n-card :title="t('quotes.linesSection')" style="margin-bottom: 16px">
                <template #header-extra>
                  <n-button
                    v-if="!isReadOnly && filteredTemplates.length > 0"
                    size="small"
                    secondary
                    @click="showTemplateModal = true"
                  >
                    <template #icon><n-icon><DocumentTextOutline /></n-icon></template>
                    {{ t('quotes.applyTemplate') }}
                  </n-button>
                </template>

                <div v-for="(line, i) in lines" :key="i" class="line-row">
                  <n-divider v-if="i > 0" />
                  <n-grid :cols="12" :x-gap="8" :y-gap="4">
                    <!-- Product selector -->
                    <n-gi :span="4">
                      <n-form-item :label="t('quotes.product')" size="small">
                        <n-select
                          :value="line.product_id"
                          filterable
                          clearable
                          remote
                          :options="productOptions.map(p => ({ label: p.name, value: p.id }))"
                          :placeholder="t('quotes.productSearch')"
                          @search="searchProducts"
                          @update:value="(v: string | null) => handleProductSelect(i, v)"
                        />
                      </n-form-item>
                    </n-gi>
                    <!-- Name -->
                    <n-gi :span="5">
                      <n-form-item :label="t('quotes.lineName')" size="small" required>
                        <n-input v-model:value="line.name" :placeholder="t('quotes.lineNamePlaceholder')" />
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
                      <n-form-item :label="t('quotes.lineDescription')" size="small">
                        <n-input v-model:value="line.description" :placeholder="t('quotes.lineDescriptionPlaceholder')" clearable />
                      </n-form-item>
                    </n-gi>
                    <!-- Qty -->
                    <n-gi :span="2">
                      <n-form-item :label="t('quotes.quantity')" size="small">
                        <n-input-number v-model:value="line.quantity" :min="0.001" :precision="3" />
                      </n-form-item>
                    </n-gi>
                    <!-- Unit name -->
                    <n-gi :span="2">
                      <n-form-item :label="t('quotes.unit')" size="small">
                        <n-input v-model:value="line.unit_name" :placeholder="t('quotes.unitPlaceholder')" clearable />
                      </n-form-item>
                    </n-gi>
                    <!-- Unit price -->
                    <n-gi :span="3">
                      <n-form-item :label="amountsIncludeVat ? t('quotes.unitPriceIncl') : t('quotes.unitPriceExcl')" size="small">
                        <n-input-number v-model:value="line.unit_price" :min="0" :precision="3" />
                      </n-form-item>
                    </n-gi>
                    <!-- Discount type -->
                    <n-gi :span="2">
                      <n-form-item :label="t('quotes.lineDiscountType')" size="small">
                        <n-select v-model:value="line.discount_type" :options="discountTypeOptions" size="small" />
                      </n-form-item>
                    </n-gi>
                    <!-- Discount value -->
                    <n-gi v-if="line.discount_type !== 'NONE'" :span="2">
                      <n-form-item :label="t('quotes.lineDiscountValue')" size="small">
                        <n-input-number v-model:value="line.discount_value" :min="0" :precision="3" size="small" />
                      </n-form-item>
                    </n-gi>
                    <!-- VAT rate (LINE mode) -->
                    <n-gi v-if="taxMode === 'LINE'" :span="3">
                      <n-form-item :label="t('quotes.vatRate')" size="small">
                        <n-select
                          v-model:value="line.vat_rate_id"
                          :options="vatRateOptions"
                          :placeholder="t('quotes.selectVatRate')"
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
                  {{ t('quotes.addLine') }}
                </n-button>
              </n-card>

              <!-- Preview totals -->
              <n-card :title="t('quotes.totalsSection')" style="margin-bottom: 16px">
                <div v-if="previewLoading" style="text-align:center; padding: 12px">
                  <n-spin />
                </div>
                <n-alert v-else-if="previewError" type="error">{{ previewError }}</n-alert>
                <div v-else-if="preview" class="totals-table">
                  <div class="total-row">
                    <span>{{ t('quotes.subtotalExclVat') }}</span>
                    <n-text>{{ fmtMoney(preview.subtotal_excl_vat) }}</n-text>
                  </div>
                  <div v-if="Number(preview.line_discount_total) !== 0" class="total-row">
                    <span>{{ t('quotes.lineDiscountTotal') }}</span>
                    <n-text>-{{ fmtMoney(preview.line_discount_total) }}</n-text>
                  </div>
                  <div v-if="Number(preview.document_discount_amount) !== 0" class="total-row">
                    <span>{{ t('quotes.documentDiscountAmount') }}</span>
                    <n-text>-{{ fmtMoney(preview.document_discount_amount) }}</n-text>
                  </div>
                  <div class="total-row">
                    <span>{{ t('quotes.taxableAmount') }}</span>
                    <n-text>{{ fmtMoney(preview.taxable_amount) }}</n-text>
                  </div>
                  <div class="total-row">
                    <span>{{ t('quotes.vatTotal') }}</span>
                    <n-text>{{ fmtMoney(preview.vat_total) }}</n-text>
                  </div>
                  <n-divider style="margin: 8px 0" />
                  <div class="total-row total-row--bold">
                    <span>{{ t('quotes.totalInclVat') }}</span>
                    <n-text strong>{{ fmtMoney(preview.total_incl_vat) }}</n-text>
                  </div>
                  <div v-if="preview.vat_treatment_snapshot" class="total-row" style="font-size: 12px; color: var(--n-text-color-3)">
                    <span>{{ t('quotes.vatTreatmentApplied') }}</span>
                    <span>{{ preview.vat_treatment_snapshot.code }}</span>
                  </div>
                </div>
                <n-text v-else depth="3">{{ t('quotes.totalsPlaceholder') }}</n-text>
              </n-card>

            </n-form>

            <!-- Action buttons -->
            <n-space justify="end" style="margin-bottom: 24px">
              <n-button @click="router.push('/quotes')">{{ t('quotes.backToList') }}</n-button>
              <n-button
                v-if="!isReadOnly"
                type="primary"
                :loading="saving"
                @click="handleSave"
              >
                {{ isEdit ? t('quotes.save') : t('quotes.create') }}
              </n-button>
            </n-space>

          </div>
        </n-spin>
      </n-layout-content>
    </n-layout>

    <!-- Document template picker modal -->
    <n-modal
      v-model:show="showTemplateModal"
      preset="card"
      :title="t('quotes.selectTemplateTitle')"
      style="max-width: 560px"
    >
      <n-list hoverable clickable>
        <n-list-item
          v-for="tmpl in filteredTemplates"
          :key="tmpl.id"
          @click="applyTemplate(tmpl)"
        >
          <n-thing :title="tmpl.name" :description="`${(tmpl.lines ?? []).length} ${t('quotes.templateLineCount')}`" />
        </n-list-item>
      </n-list>
      <template #footer>
        <n-space justify="end">
          <n-button @click="showTemplateModal = false">{{ t('quotes.cancel') }}</n-button>
        </n-space>
      </template>
    </n-modal>

    <!-- Note template picker modal -->
    <n-modal
      v-model:show="showNoteTemplateModal"
      preset="card"
      :title="t('quotes.selectNoteTemplateTitle')"
      style="max-width: 480px"
    >
      <n-list hoverable clickable>
        <n-list-item
          v-for="nt in noteTemplates"
          :key="nt.id"
          @click="insertNoteTemplate(nt.body)"
        >
          <n-thing :title="nt.name" :description="nt.body.slice(0, 80) + (nt.body.length > 80 ? '…' : '')" />
        </n-list-item>
      </n-list>
      <template #footer>
        <n-space justify="end">
          <n-button @click="showNoteTemplateModal = false">{{ t('quotes.cancel') }}</n-button>
        </n-space>
      </template>
    </n-modal>

    <!-- Reactivate modal -->
    <n-modal
      v-model:show="showReactivateModal"
      preset="card"
      :title="t('quotes.reactivateModalTitle')"
      style="max-width: 400px"
    >
      <n-form label-placement="top">
        <n-form-item :label="t('quotes.reactivateNewDate')" required>
          <n-date-picker
            v-model:formatted-value="reactivateDate"
            value-format="yyyy-MM-dd"
            type="date"
            :is-date-disabled="isDateDisabled"
            clearable
            style="width: 100%"
          />
        </n-form-item>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button @click="showReactivateModal = false">{{ t('quotes.cancel') }}</n-button>
          <n-button
            type="primary"
            :loading="reactivating"
            :disabled="!reactivateDate"
            @click="confirmReactivate"
          >
            {{ t('quotes.confirm') }}
          </n-button>
        </n-space>
      </template>
    </n-modal>
  </div>
</template>

<style scoped>
.app-content {
  min-height: calc(100vh - 57px);
}

.quote-edit-container {
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
