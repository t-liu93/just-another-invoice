<script setup lang="ts">
import { onMounted, ref, computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { localDateStr } from '../../utils/date'
import {
  useMessage,
  NButton, NSpace, NInput, NForm, NFormItem, NCard, NSpin, NAlert,
  NDivider, NInputNumber, NSelect, NSwitch, NTag, NDatePicker,
  NGrid, NGi, NText, NModal, NList, NListItem, NThing, NDropdown,
} from 'naive-ui'
import { AddOutline, TrashOutline, DocumentTextOutline, DownloadOutline, MailOutline, EyeOutline } from '@vicons/ionicons5'
import { NIcon } from 'naive-ui'
import InvoicePaymentPanel from '../../components/InvoicePaymentPanel.vue'
import DocumentSendDialog from '../../components/DocumentSendDialog.vue'
import PdfPreviewDialog from '../../components/PdfPreviewDialog.vue'
import EmailLogPanel from '../../components/EmailLogPanel.vue'
import { useInvoicesStore } from '../../stores/invoices'
import type { InvoicePaymentsResponse } from '../../stores/payments'
import { get, downloadBlob } from '../../api/http'
import {
  createDocumentChainPaymentChangeHandler,
  useDocumentChainRefresh,
} from '../../composables/useDocumentChainRefresh'
import type { components } from '../../api/schema'
import { persistedReceiptCustomer, receiptAuditTarget } from '../../utils/receiptEmail'
import { invoiceDocumentKindLabelKey } from '../../utils/documentKind'
import { invoiceDocumentSendType } from '../../utils/documentSend'

type CustomerRead = components['schemas']['CustomerRead']
type VatRateRead = components['schemas']['VatRateRead']
type VatTreatmentRead = components['schemas']['VatTreatmentRead']
type ProductInvoiceOptionRead = components['schemas']['ProductInvoiceOptionRead']
type InvoiceCalculationRead = components['schemas']['InvoiceCalculationRead']
type InvoiceWrite = components['schemas']['InvoiceWrite']
type InvoiceRead = components['schemas']['InvoiceRead']
type DocumentChainRead = components['schemas']['DocumentChainRead']
type DocumentTemplateRead = components['schemas']['DocumentTemplateRead']
type ContentBlockRead = components['schemas']['ContentBlockRead']
type NoteTemplateRead = components['schemas']['NoteTemplateRead']
type EmailLogRead = components['schemas']['EmailLogRead']

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
const {
  documentChain,
  chainRefreshing,
  initialChainError,
  paymentRefreshError,
  loadInitialDocumentChain,
  refreshAfterPayment,
} = useDocumentChainRefresh<DocumentChainRead>(async () => {
  const invoiceId = existingInvoice.value?.id
  if (!invoiceId) throw new Error('Invoice document chain is unavailable')
  return get<DocumentChainRead>(`/api/v1/invoices/${invoiceId}/document-chain`)
})

// Invoice header fields.
const customerId = ref<string | null>(null)
const customers = ref<CustomerRead[]>([])
const selectedCustomer = ref<CustomerRead | null>(null)

const referenceNumber = ref<string | null>(null)
const invoiceDate = ref(localDateStr(new Date()))
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

// Content block / template state
const warrantyText = ref<string | null>(null)
const termsText = ref<string | null>(null)
const bankText = ref<string | null>(null)
const paymentTermsText = ref<string | null>(null)
const documentTemplates = ref<DocumentTemplateRead[]>([])
const contentBlocks = ref<ContentBlockRead[]>([])
const noteTemplates = ref<NoteTemplateRead[]>([])
const showTemplateModal = ref(false)
const showNoteTemplateModal = ref(false)

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

const filteredTemplates = computed(() =>
  documentTemplates.value.filter(t => t.applies_to === 'INVOICE' || t.applies_to === 'BOTH')
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

function insertNoteTemplate(body: string) {
  notes.value = notes.value ? `${notes.value}\n${body}` : body
  showNoteTemplateModal.value = false
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

const discountTypeOptions = computed(() => [
  { label: t('common.discountNone'), value: 'NONE' },
  { label: t('common.discountPercentage'), value: 'PERCENTAGE' },
  { label: t('common.discountFixed'), value: 'FIXED' },
])

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
  warrantyText.value = inv.warranty_text ?? null
  termsText.value = inv.terms_text ?? null
  bankText.value = inv.bank_text ?? null
  paymentTermsText.value = inv.payment_terms_text ?? null
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
      loadContentLibraries(),
      searchCustomers(''),
      store.fetchProductOptions().then(opts => { productOptions.value = opts }),
    ])

    const id = route.params.id as string | undefined
    if (id && id !== 'new') {
      isEdit.value = true
      const inv = await store.fetchInvoice(id)
      populateFromInvoice(inv)
      await loadInitialDocumentChain()
      // Ensure the selected customer is in the list even if not returned by the initial search.
      if (!customers.value.find(c => c.id === inv.customer_id)) {
        const custRes = await get<CustomerRead>(`/api/v1/customers/${inv.customer_id}`)
        customers.value = [custRes, ...customers.value]
      }
    } else {
      applyDefaultContentBlocks()
    }
  } catch (e: unknown) {
    pageError.value = e instanceof Error ? e.message : String(e)
  } finally {
    pageLoading.value = false
  }
})

const isReadOnly = computed(() => existingInvoice.value?.status !== 'DRAFT' && isEdit.value)

// Show payment panel only when editing an existing invoice that is SENT or COMPLETED
const showPaymentPanel = computed(() =>
  isEdit.value &&
  existingInvoice.value !== null &&
  (existingInvoice.value.status === 'SENT' || existingInvoice.value.status === 'COMPLETED' || existingInvoice.value.status === 'DRAFT' || existingInvoice.value.status === 'CANCELLED'),
)

// Payment receipts are always addressed from the persisted invoice source,
// never from a customer choice that is still unsaved in this edit form.
const receiptCustomer = computed(() =>
  persistedReceiptCustomer(existingInvoice.value?.customer_id, customers.value),
)

const handlePaymentsChanged = createDocumentChainPaymentChangeHandler<InvoicePaymentsResponse>(
  refreshAfterPayment,
  (aggregate) => {
  // Update the displayed invoice status and paid_status from the backend aggregate
  // (the aggregate is authoritative – frontend does not locally compute these)
  if (existingInvoice.value) {
    existingInvoice.value = {
      ...existingInvoice.value,
      paid_status: aggregate.paid_status,
      status: aggregate.status,
      due_amount: aggregate.due_amount,
    }
  }
  },
)

const fmtMoney = (v: string | number) => Number(v).toFixed(2)

// ---- PDF download ----
const downloadingPdf = ref(false)

const pdfLocaleOptions = computed(() => [
  { label: t('pdf.localeDefault'), key: 'default' },
  { label: t('pdf.localeEn'), key: 'en' },
  { label: t('pdf.localeZh'), key: 'zh' },
])

async function handleDownloadPdf(locale?: 'en' | 'zh') {
  if (!existingInvoice.value) return
  downloadingPdf.value = true
  try {
    const id = existingInvoice.value.id
    const url = locale
      ? `/api/v1/invoices/${id}/pdf?locale=${locale}`
      : `/api/v1/invoices/${id}/pdf`
    await downloadBlob(url, `${existingInvoice.value.invoice_number ?? 'concept'}.pdf`)
  } catch (e: unknown) {
    message.error(e instanceof Error ? e.message : t('pdf.downloadFailed'))
  } finally {
    downloadingPdf.value = false
  }
}

function handlePdfLocaleSelect(key: string) {
  if (key === 'default') {
    handleDownloadPdf()
  } else {
    handleDownloadPdf(key as 'en' | 'zh')
  }
}

// ---- PDF preview (in-app modal) ----
const previewShow = ref(false)
const previewSrc = ref<string | null>(null)
const previewFallback = ref('invoice.pdf')

function openPreview(locale?: 'en' | 'zh') {
  if (!existingInvoice.value) return
  const id = existingInvoice.value.id
  previewSrc.value = locale
    ? `/api/v1/invoices/${id}/pdf?preview=true&locale=${locale}`
    : `/api/v1/invoices/${id}/pdf?preview=true`
  previewFallback.value = `${existingInvoice.value.invoice_number ?? 'concept'}.pdf`
  previewShow.value = true
}

function handlePreviewLocaleSelect(key: string) {
  if (key === 'default') {
    openPreview()
  } else {
    openPreview(key as 'en' | 'zh')
  }
}

// ---- Send dialog ----
const sendDialogShow = ref(false)

function openSendDialog() {
  sendDialogShow.value = true
}

// ---- Email log panel ref ----
const emailLogPanelRef = ref<InstanceType<typeof EmailLogPanel> | null>(null)
const receiptQuoteAuditId = ref<string | null>(null)

function handleSent(_log: EmailLogRead) {
  // Refresh email log panel after successful send
  emailLogPanelRef.value?.refresh()
}

function handleReceiptSent(log: EmailLogRead) {
  if (!existingInvoice.value) return
  const target = receiptAuditTarget(existingInvoice.value.id, log)
  if (target === 'refresh-invoice') {
    emailLogPanelRef.value?.refresh()
    receiptQuoteAuditId.value = null
  } else if (target !== null) {
    receiptQuoteAuditId.value = target.quoteId
  }
}
</script>

<template>
  <div class="invoice-edit-page">
    <n-spin :show="pageLoading">
          <div class="invoice-edit-container">

            <!-- Page title + status actions -->
            <div class="page-header">
              <h2>
                {{ isEdit
                  ? (existingInvoice ? (existingInvoice.invoice_number ?? t('invoices.concept')) : t('invoices.edit'))
                  : t('invoices.new') }}
              </h2>
              <n-space v-if="existingInvoice" align="center">
                <n-tag size="small">
                  {{ t(invoiceDocumentKindLabelKey(existingInvoice.document_kind)) }}
                </n-tag>
                <n-tag :type="existingInvoice.status === 'DRAFT' ? 'default' : existingInvoice.status === 'SENT' ? 'info' : existingInvoice.status === 'COMPLETED' ? 'success' : 'warning'">
                  {{ t(`invoices.status${existingInvoice.status}`) }}
                </n-tag>
                <n-tag :type="existingInvoice.paid_status === 'PAID' ? 'success' : existingInvoice.paid_status === 'PARTIALLY_PAID' ? 'warning' : 'default'">
                  {{ t(`invoices.paidStatus${existingInvoice.paid_status}`) }}
                </n-tag>
                <n-text depth="3" style="font-size: 13px">
                  {{ t('invoices.due') }}: {{ existingInvoice.currency }} {{ fmtMoney(existingInvoice.due_amount) }}
                </n-text>

                <!-- PDF preview dropdown (default / en / zh) -->
                <n-dropdown
                  :options="pdfLocaleOptions"
                  trigger="click"
                  @select="handlePreviewLocaleSelect"
                >
                  <n-button size="small">
                    <template #icon><n-icon><EyeOutline /></n-icon></template>
                    {{ t('pdf.preview') }}
                  </n-button>
                </n-dropdown>

                <!-- PDF download dropdown (default / en / zh) -->
                <n-dropdown
                  :options="pdfLocaleOptions"
                  trigger="click"
                  @select="handlePdfLocaleSelect"
                >
                  <!-- v-if/v-else pattern to avoid loading-prop+v-if prod bug -->
                  <n-button v-if="downloadingPdf" size="small" loading disabled>
                    {{ t('pdf.download') }}
                  </n-button>
                  <n-button v-else size="small">
                    <template #icon><n-icon><DownloadOutline /></n-icon></template>
                    {{ t('pdf.download') }}
                  </n-button>
                </n-dropdown>

                <!-- Send email button -->
                <n-button size="small" type="info" @click="openSendDialog">
                  <template #icon><n-icon><MailOutline /></n-icon></template>
                  {{ t('sendDialog.title') }}
                </n-button>

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

            <n-card v-if="documentChain || chainRefreshing || initialChainError || paymentRefreshError" size="small" class="chain-card">
              <n-space vertical size="small">
                <n-text v-if="documentChain" strong>{{ t('chain.billingMode') }}: {{ documentChain.settlement_mode }}</n-text>
                <n-text depth="3">{{ t('chain.readOnly') }}</n-text>
                <n-spin v-if="chainRefreshing" size="small" />
                <n-alert v-if="initialChainError" type="error">
                  {{ t('chain.initialLoadFailed') }}
                </n-alert>
                <n-button v-if="initialChainError" text type="primary" :loading="chainRefreshing" @click="loadInitialDocumentChain">
                  {{ t('chain.retry') }}
                </n-button>
                <n-alert v-if="paymentRefreshError" type="error">
                  {{ t('chain.paymentRefreshFailed') }}
                </n-alert>
                <n-button v-if="paymentRefreshError" text type="primary" :loading="chainRefreshing" @click="refreshAfterPayment">
                  {{ t('chain.retry') }}
                </n-button>
                <template v-if="documentChain?.relations.length">
                  <n-text strong>{{ t('chain.relations') }}</n-text>
                  <n-list bordered>
                    <n-list-item v-for="relation in documentChain.relations" :key="`${relation.relation_type}:${relation.from_node_id}:${relation.to_node_id}`">
                      {{ relation.relation_type }} · {{ relation.from_node_id }} → {{ relation.to_node_id }}
                    </n-list-item>
                  </n-list>
                </template>
                <n-list v-if="documentChain" bordered>
                  <n-list-item v-for="event in documentChain.events" :key="event.id">
                    {{ event.event_type }} · {{ event.occurred_at }}
                  </n-list-item>
                </n-list>
              </n-space>
            </n-card>

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
                      <div style="width: 100%">
                        <n-input v-model:value="notes" type="textarea" :autosize="{ minRows: 2 }" clearable />
                        <n-button
                          v-if="!isReadOnly && noteTemplates.length > 0"
                          size="small"
                          secondary
                          style="margin-top: 4px"
                          @click="showNoteTemplateModal = true"
                        >
                          {{ t('invoices.insertNoteTemplate') }}
                        </n-button>
                      </div>
                    </n-form-item>
                  </n-gi>
                </n-grid>
              </n-card>

              <!-- Content blocks -->
              <n-card :title="t('invoices.contentSection')" style="margin-bottom: 16px">
                <!-- Warranty -->
                <n-form-item :label="t('invoices.warrantyText')">
                  <div style="width: 100%">
                    <div v-if="!isReadOnly && contentBlocksByKind['WARRANTY']?.length" style="margin-bottom: 4px">
                      <n-select
                        :options="(contentBlocksByKind['WARRANTY'] ?? []).map(b => ({ label: b.name, value: b.id }))"
                        :placeholder="t('invoices.selectContentBlock')"
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
                <n-form-item :label="t('invoices.termsText')">
                  <div style="width: 100%">
                    <div v-if="!isReadOnly && contentBlocksByKind['TERMS']?.length" style="margin-bottom: 4px">
                      <n-select
                        :options="(contentBlocksByKind['TERMS'] ?? []).map(b => ({ label: b.name, value: b.id }))"
                        :placeholder="t('invoices.selectContentBlock')"
                        size="small"
                        style="width: 250px"
                        clearable
                        @update:value="(v: string) => selectContentBlock('TERMS', v)"
                      />
                    </div>
                    <n-input v-model:value="termsText" type="textarea" :autosize="{ minRows: 2 }" clearable />
                  </div>
                </n-form-item>
                <!-- Bank -->
                <n-form-item :label="t('invoices.bankText')">
                  <div style="width: 100%">
                    <div v-if="!isReadOnly && contentBlocksByKind['BANK']?.length" style="margin-bottom: 4px">
                      <n-select
                        :options="(contentBlocksByKind['BANK'] ?? []).map(b => ({ label: b.name, value: b.id }))"
                        :placeholder="t('invoices.selectContentBlock')"
                        size="small"
                        style="width: 250px"
                        clearable
                        @update:value="(v: string) => selectContentBlock('BANK', v)"
                      />
                    </div>
                    <n-input v-model:value="bankText" type="textarea" :autosize="{ minRows: 2 }" clearable />
                  </div>
                </n-form-item>
                <!-- Payment Terms -->
                <n-form-item :label="t('invoices.paymentTermsText')">
                  <div style="width: 100%">
                    <div v-if="!isReadOnly && contentBlocksByKind['PAYMENT_TERMS']?.length" style="margin-bottom: 4px">
                      <n-select
                        :options="(contentBlocksByKind['PAYMENT_TERMS'] ?? []).map(b => ({ label: b.name, value: b.id }))"
                        :placeholder="t('invoices.selectContentBlock')"
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
              <n-card :title="t('invoices.linesSection')" style="margin-bottom: 16px">
                <template #header-extra>
                  <n-button
                    v-if="!isReadOnly && filteredTemplates.length > 0"
                    size="small"
                    secondary
                    @click="showTemplateModal = true"
                  >
                    <template #icon><n-icon><DocumentTextOutline /></n-icon></template>
                    {{ t('invoices.applyTemplate') }}
                  </n-button>
                </template>
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
                        <n-input v-model:value="line.description" type="textarea" :autosize="{ minRows: 1, maxRows: 6 }" :placeholder="t('invoices.lineDescriptionPlaceholder')" clearable />
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

            <!-- Payment panel (only for existing SENT/COMPLETED/DRAFT/CANCELLED invoices – panel guards internally) -->
            <InvoicePaymentPanel
              v-if="showPaymentPanel && existingInvoice"
              :invoice-id="existingInvoice.id"
              :invoice-status="existingInvoice.status"
              :customer-email="receiptCustomer?.email ?? null"
              :customer-locale="receiptCustomer?.locale ?? null"
              @payments-changed="handlePaymentsChanged"
              @receipt-sent="handleReceiptSent"
            />

            <!-- Email log (only for existing invoices) -->
            <EmailLogPanel
              v-if="isEdit && existingInvoice"
              ref="emailLogPanelRef"
              doc-type="invoice"
              :doc-id="existingInvoice.id"
            />
            <n-alert v-if="receiptQuoteAuditId" type="info" style="margin-bottom: 16px">
              {{ t('payments.receiptAuditOnQuote') }}
              <n-button text type="primary" @click="router.push(`/quotes/${receiptQuoteAuditId}/edit`)">
                {{ t('payments.openSourceQuoteLog') }}
              </n-button>
            </n-alert>

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

    <!-- Document template picker modal -->
    <n-modal
      v-model:show="showTemplateModal"
      preset="card"
      :title="t('invoices.selectTemplateTitle')"
      style="max-width: 560px"
    >
      <n-list hoverable clickable>
        <n-list-item
          v-for="tmpl in filteredTemplates"
          :key="tmpl.id"
          @click="applyTemplate(tmpl)"
        >
          <n-thing :title="tmpl.name" :description="`${(tmpl.lines ?? []).length} ${t('invoices.templateLineCount')}`" />
        </n-list-item>
      </n-list>
      <template #footer>
        <n-space justify="end">
          <n-button @click="showTemplateModal = false">{{ t('invoices.cancel') }}</n-button>
        </n-space>
      </template>
    </n-modal>

    <!-- Note template picker modal -->
    <n-modal
      v-model:show="showNoteTemplateModal"
      preset="card"
      :title="t('invoices.selectNoteTemplateTitle')"
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
          <n-button @click="showNoteTemplateModal = false">{{ t('invoices.cancel') }}</n-button>
        </n-space>
      </template>
    </n-modal>

    <!-- Send email dialog (only when editing an existing invoice) -->
    <DocumentSendDialog
      v-if="isEdit && existingInvoice"
      v-model:show="sendDialogShow"
      :doc-type="invoiceDocumentSendType(existingInvoice.document_kind)"
      :doc-id="existingInvoice.id"
      :customer-email="selectedCustomer?.email ?? null"
      :customer-locale="existingInvoice.party_snapshot_locale"
      @sent="handleSent"
    />

    <!-- PDF preview dialog -->
    <PdfPreviewDialog
      v-model:show="previewShow"
      :src="previewSrc"
      :fallback-filename="previewFallback"
    />
  </div>
</template>

<style scoped>

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
