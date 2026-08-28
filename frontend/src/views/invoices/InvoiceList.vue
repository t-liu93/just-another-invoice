<script setup lang="ts">
import { onMounted, onBeforeUnmount, computed, h, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import {
  useMessage, useDialog,
  NButton, NSpace, NInput, NDataTable, NAlert, NSpin,
  NPagination, NSelect, NTag, NDropdown,
} from 'naive-ui'
import { AddOutline, SearchOutline, CreateOutline, TrashOutline, DownloadOutline, MailOutline, EyeOutline } from '@vicons/ionicons5'
import { NIcon } from 'naive-ui'
import DocumentSendDialog from '../../components/DocumentSendDialog.vue'
import PdfPreviewDialog from '../../components/PdfPreviewDialog.vue'
import { useInvoicesStore } from '../../stores/invoices'
import type { InvoiceListItem } from '../../stores/invoices'
import { get, downloadBlob } from '../../api/http'
import type { components } from '../../api/schema'
import { invoiceDocumentKindLabelKey } from '../../utils/documentKind'

type CustomerRead = components['schemas']['CustomerRead']
type EmailLogRead = components['schemas']['EmailLogRead']

const router = useRouter()
const { t } = useI18n()
const message = useMessage()
const dialog = useDialog()
const store = useInvoicesStore()

// Send dialog state
const sendDialogShow = ref(false)
const sendDialogInvoiceId = ref('')
const sendDialogCustomerEmail = ref<string | null>(null)
const sendDialogCustomerLocale = ref<'en' | 'zh' | null>(null)

async function openSendDialog(row: InvoiceListItem) {
  sendDialogInvoiceId.value = row.id
  sendDialogCustomerEmail.value = null
  sendDialogCustomerLocale.value = null
  sendDialogShow.value = true
  // Fetch customer info for email/locale pre-fill
  try {
    const cust = await get<CustomerRead>(`/api/v1/customers/${row.customer_id}`)
    sendDialogCustomerEmail.value = cust.email ?? null
    sendDialogCustomerLocale.value = (cust.locale as 'en' | 'zh' | null | undefined) ?? null
  } catch {
    // Non-critical: dialog pre-fill is best-effort
  }
}

function handleSent(_log: EmailLogRead) {
  // Log was created; no further action needed in list view
}

// Download PDF for a list row
const downloadingId = ref<string | null>(null)

async function handleDownloadPdf(id: string, locale?: 'en' | 'zh') {
  downloadingId.value = id
  try {
    const url = locale
      ? `/api/v1/invoices/${id}/pdf?locale=${locale}`
      : `/api/v1/invoices/${id}/pdf`
    await downloadBlob(url, `invoice-${id}.pdf`)
  } catch (e: unknown) {
    message.error(e instanceof Error ? e.message : t('pdf.downloadFailed'))
  } finally {
    downloadingId.value = null
  }
}

function pdfLocaleOptions(id: string) {
  return [
    { label: t('pdf.localeDefault'), key: `${id}:default` },
    { label: t('pdf.localeEn'), key: `${id}:en` },
    { label: t('pdf.localeZh'), key: `${id}:zh` },
  ]
}

function handlePdfLocaleSelect(key: string) {
  const [id, locale] = key.split(':')
  if (locale === 'default') {
    handleDownloadPdf(id)
  } else {
    handleDownloadPdf(id, locale as 'en' | 'zh')
  }
}

// PDF preview (in-app modal)
const previewShow = ref(false)
const previewSrc = ref<string | null>(null)
const previewFallback = ref('invoice.pdf')

function openPreview(id: string, locale?: 'en' | 'zh') {
  previewSrc.value = locale
    ? `/api/v1/invoices/${id}/pdf?locale=${locale}`
    : `/api/v1/invoices/${id}/pdf`
  previewFallback.value = `invoice-${id}.pdf`
  previewShow.value = true
}

function handlePreviewLocaleSelect(key: string) {
  const [id, locale] = key.split(':')
  if (locale === 'default') {
    openPreview(id)
  } else {
    openPreview(id, locale as 'en' | 'zh')
  }
}

// Customer filter state
const customers = ref<CustomerRead[]>([])

async function searchCustomers(q: string) {
  const res = await get<{ items: CustomerRead[] }>(`/api/v1/customers?q=${encodeURIComponent(q)}&limit=30`)
  customers.value = res.items
}

const customerOptions = computed(() => customers.value.map(c => ({ label: c.name, value: c.id })))

function handleCustomerFilter(val: string | null) {
  store.customerIdFilter = val
  store.offset = 0
  store.fetchInvoices()
}

onMounted(() => {
  store.fetchInvoices()
  searchCustomers('')
})

let searchTimer: ReturnType<typeof setTimeout> | null = null

function handleSearchInput() {
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(() => {
    store.offset = 0
    store.fetchInvoices()
  }, 300)
}

function handleSearch() {
  if (searchTimer) clearTimeout(searchTimer)
  store.offset = 0
  store.fetchInvoices()
}

onBeforeUnmount(() => {
  if (searchTimer) clearTimeout(searchTimer)
})

function handlePageChange(page: number) {
  store.offset = (page - 1) * store.limit
  store.fetchInvoices()
}

function handleCreate() {
  router.push('/invoices/new')
}

function handleEdit(id: string) {
  router.push(`/invoices/${id}/edit`)
}

function handleDelete(invoiceNumber: string, id: string) {
  dialog.warning({
    title: t('invoices.delete'),
    content: t('invoices.deleteConfirm', { number: invoiceNumber }),
    positiveText: t('invoices.delete'),
    negativeText: t('invoices.cancel'),
    onPositiveClick: async () => {
      try {
        await store.deleteInvoice(id)
        message.success(t('invoices.deleteSuccess'))
        store.fetchInvoices()
      } catch {
        message.error(t('invoices.deleteFailed'))
      }
    },
  })
}

const currentPage = computed(() => Math.floor(store.offset / store.limit) + 1)
const pageCount = computed(() => Math.max(1, Math.ceil(store.total / store.limit)))

const sortOptions = computed(() => [
  { label: t('invoices.sortByDate'), value: 'invoice_date' },
  { label: t('invoices.sortByCreatedAt'), value: 'created_at' },
  { label: t('invoices.sortByNumber'), value: 'invoice_number' },
])

const statusOptions = computed(() => [
  { label: t('invoices.statusDRAFT'), value: 'DRAFT' },
  { label: t('invoices.statusSENT'), value: 'SENT' },
  { label: t('invoices.statusCANCELLED'), value: 'CANCELLED' },
  { label: t('invoices.statusCOMPLETED'), value: 'COMPLETED' },
])

const paidStatusOptions = computed(() => [
  { label: t('invoices.paidStatusUNPAID'), value: 'UNPAID' },
  { label: t('invoices.paidStatusPARTIALLY_PAID'), value: 'PARTIALLY_PAID' },
  { label: t('invoices.paidStatusPAID'), value: 'PAID' },
])

function handleSortChange(val: 'invoice_date' | 'created_at' | 'invoice_number') {
  store.sortBy = val
  store.offset = 0
  store.fetchInvoices()
}

function handleStatusFilter(val: string | null) {
  store.statusFilter = val
  store.offset = 0
  store.fetchInvoices()
}

function handlePaidStatusFilter(val: string | null) {
  store.paidStatusFilter = val
  store.offset = 0
  store.fetchInvoices()
}

function statusTagType(status: string): 'default' | 'info' | 'success' | 'warning' | 'error' {
  const map: Record<string, 'default' | 'info' | 'success' | 'warning' | 'error'> = {
    DRAFT: 'default',
    SENT: 'info',
    COMPLETED: 'success',
    CANCELLED: 'warning',
  }
  return map[status] ?? 'default'
}

function paidStatusTagType(ps: string): 'default' | 'warning' | 'success' {
  const map: Record<string, 'default' | 'warning' | 'success'> = {
    UNPAID: 'default',
    PARTIALLY_PAID: 'warning',
    PAID: 'success',
  }
  return map[ps] ?? 'default'
}

const columns = computed(() => [
  {
    title: t('invoices.invoiceNumber'),
    key: 'invoice_number',
    width: 140,
    render(row: InvoiceListItem) {
      return row.invoice_number ?? t('invoices.concept')
    },
  },
  {
    title: t('invoices.customer'),
    key: 'customer_name',
    ellipsis: { tooltip: true },
  },
  {
    title: t('invoices.invoiceDate'),
    key: 'invoice_date',
    width: 110,
  },
  {
    title: t('invoices.documentKind'),
    key: 'document_kind',
    width: 130,
    render(row: InvoiceListItem) {
      return h(NTag, { size: 'small' }, () => t(invoiceDocumentKindLabelKey(row.document_kind)))
    },
  },
  {
    title: t('invoices.status'),
    key: 'status',
    width: 100,
    render(row: InvoiceListItem) {
      return h(NTag, { type: statusTagType(row.status), size: 'small' }, () => t(`invoices.status${row.status}`))
    },
  },
  {
    title: t('invoices.paidStatus'),
    key: 'paid_status',
    width: 120,
    render(row: InvoiceListItem) {
      return h(NTag, { type: paidStatusTagType(row.paid_status), size: 'small' }, () => t(`invoices.paidStatus${row.paid_status}`))
    },
  },
  {
    title: t('invoices.totalInclVat'),
    key: 'total_incl_vat',
    align: 'right' as const,
    width: 120,
    render(row: InvoiceListItem) {
      return h('span', {}, `${row.currency} ${Number(row.total_incl_vat).toFixed(2)}`)
    },
  },
  {
    title: t('invoices.actions'),
    key: 'actions',
    width: 188,
    align: 'center' as const,
    render(row: InvoiceListItem) {
      const isEditable = row.status === 'DRAFT'
      return h(NSpace, { size: 4, justify: 'center', wrapItem: false }, () => [
        h(
          NButton,
          {
            size: 'small',
            quaternary: true,
            circle: true,
            title: isEditable ? t('invoices.edit') : t('invoices.view'),
            onClick: () => handleEdit(row.id),
          },
          () => h(NIcon, null, { default: () => h(CreateOutline) }),
        ),
        // PDF preview dropdown (default / en / zh)
        h(
          NDropdown,
          {
            options: pdfLocaleOptions(row.id),
            trigger: 'click',
            onSelect: handlePreviewLocaleSelect,
          },
          () => h(
            NButton,
            {
              size: 'small',
              quaternary: true,
              circle: true,
              title: t('pdf.preview'),
            },
            () => h(NIcon, null, { default: () => h(EyeOutline) }),
          ),
        ),
        // PDF download dropdown (default / en / zh)
        h(
          NDropdown,
          {
            options: pdfLocaleOptions(row.id),
            trigger: 'click',
            onSelect: handlePdfLocaleSelect,
          },
          () => h(
            NButton,
            {
              size: 'small',
              quaternary: true,
              circle: true,
              title: t('pdf.download'),
              loading: downloadingId.value === row.id,
              disabled: downloadingId.value === row.id,
            },
            () => h(NIcon, null, { default: () => h(DownloadOutline) }),
          ),
        ),
        // Send email button
        h(
          NButton,
          {
            size: 'small',
            quaternary: true,
            circle: true,
            title: t('sendDialog.title'),
            onClick: () => openSendDialog(row),
          },
          () => h(NIcon, null, { default: () => h(MailOutline) }),
        ),
        ...(isEditable ? [h(
          NButton,
          {
            size: 'small',
            quaternary: true,
            circle: true,
            type: 'error',
            title: t('invoices.delete'),
            onClick: () => handleDelete(row.invoice_number ?? t('invoices.concept'), row.id),
          },
          () => h(NIcon, null, { default: () => h(TrashOutline) }),
        )] : []),
      ])
    },
  },
])
</script>

<template>
  <div class="invoice-list-page">
    <div class="invoice-list-container">
          <div class="invoice-list-header">
            <h2>{{ t('invoices.title') }}</h2>
            <n-space align="center" wrap>
              <n-input
                v-model:value="store.query"
                :placeholder="t('invoices.search')"
                clearable
                style="width: 200px"
                @input="handleSearchInput"
                @keyup.enter="handleSearch"
                @clear="handleSearch"
              >
                <template #prefix>
                  <n-icon><SearchOutline /></n-icon>
                </template>
              </n-input>
              <n-select
                :value="store.customerIdFilter"
                :options="customerOptions"
                :placeholder="t('invoices.customerAll')"
                style="width: 160px"
                filterable
                clearable
                @search="searchCustomers"
                @update:value="handleCustomerFilter"
              />
              <n-select
                :value="store.statusFilter"
                :options="statusOptions"
                :placeholder="t('invoices.statusAll')"
                style="width: 120px"
                clearable
                @update:value="handleStatusFilter"
              />
              <n-select
                :value="store.paidStatusFilter"
                :options="paidStatusOptions"
                :placeholder="t('invoices.paidStatusAll')"
                style="width: 130px"
                clearable
                @update:value="handlePaidStatusFilter"
              />
              <n-select
                :value="store.sortBy"
                :options="sortOptions"
                style="width: 140px"
                @update:value="handleSortChange"
              />
              <n-button type="primary" @click="handleCreate">
                <template #icon>
                  <n-icon><AddOutline /></n-icon>
                </template>
                {{ t('invoices.create') }}
              </n-button>
            </n-space>
          </div>

          <n-alert v-if="store.error" type="error" style="margin-bottom: 16px">
            {{ store.error }}
          </n-alert>

          <n-spin :show="store.loading">
            <template v-if="!store.loading && store.items.length === 0">
              <n-empty
                v-if="store.total === 0 && !store.query && !store.statusFilter && !store.customerIdFilter && !store.paidStatusFilter"
                :description="t('invoices.empty')"
              />
              <n-empty v-else :description="t('invoices.noResults')" />
            </template>

            <n-data-table
              v-else
              :columns="columns"
              :data="store.items"
              :bordered="false"
              :row-key="(row: InvoiceListItem) => row.id"
              striped
            />

            <div v-if="store.total > store.limit" class="pagination-container">
              <n-pagination
                :page="currentPage"
                :page-count="pageCount"
                @update:page="handlePageChange"
              />
            </div>
          </n-spin>
    </div>

    <!-- Send email dialog -->
    <DocumentSendDialog
      v-model:show="sendDialogShow"
      doc-type="invoice"
      :doc-id="sendDialogInvoiceId"
      :customer-email="sendDialogCustomerEmail"
      :customer-locale="sendDialogCustomerLocale"
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
.invoice-list-container {
  max-width: 1200px;
  margin: 0 auto;
  padding: 24px;
}

.invoice-list-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 16px;
  flex-wrap: wrap;
  gap: 12px;
}

.invoice-list-header h2 {
  margin: 0;
}

.pagination-container {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}
</style>
