<script setup lang="ts">
import { onMounted, onBeforeUnmount, computed, h, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import {
  NButton, NSpace, NInput, NDataTable, NAlert, NSpin,
  NPagination, NSelect, NText, NDatePicker,
} from 'naive-ui'
import { SearchOutline } from '@vicons/ionicons5'
import { NIcon } from 'naive-ui'
import { usePaymentsStore } from '../../stores/payments'
import type { PaymentListItem } from '../../stores/payments'
import { get } from '../../api/http'
import type { components } from '../../api/schema'
import { localDateStr, formatDate } from '../../utils/date'

type CustomerRead = components['schemas']['CustomerRead']
type PaymentMethodRead = components['schemas']['PaymentMethodRead']
type PaymentMethodListResponse = components['schemas']['PaymentMethodListResponse']

const router = useRouter()
const { t } = useI18n()
const store = usePaymentsStore()

const customers = ref<CustomerRead[]>([])
const paymentMethods = ref<PaymentMethodRead[]>([])

async function searchCustomers(q: string) {
  const res = await get<{ items: CustomerRead[] }>(`/api/v1/customers?q=${encodeURIComponent(q)}&limit=30`)
  customers.value = res.items
}

async function loadPaymentMethods() {
  const res = await get<PaymentMethodListResponse>('/api/v1/payment-methods')
  paymentMethods.value = res.items
}

const customerOptions = computed(() => customers.value.map(c => ({ label: c.name, value: c.id })))
// Filter context: list ALL methods (incl. inactive) so historical payments referencing a now-deactivated method stay filterable; the record form (panel) lists active only.
const paymentMethodOptions = computed(() => paymentMethods.value.map(m => ({ label: m.name, value: m.id })))

function handleCustomerFilter(val: string | null) {
  store.customerIdFilter = val
  store.offset = 0
  void store.fetchPayments()
}

function handleMethodFilter(val: string | null) {
  store.paymentMethodIdFilter = val
  store.offset = 0
  void store.fetchPayments()
}

// Date range: NDatePicker with type="daterange" returns [ts, ts] | null
const dateRange = ref<[number, number] | null>(null)

function handleDateRange(val: [number, number] | null) {
  if (val) {
    store.dateFrom = localDateStr(new Date(val[0]))
    store.dateTo = localDateStr(new Date(val[1]))
  } else {
    store.dateFrom = null
    store.dateTo = null
  }
  store.offset = 0
  void store.fetchPayments()
}

onMounted(() => {
  void store.fetchPayments()
  void searchCustomers('')
  void loadPaymentMethods()
})

let searchTimer: ReturnType<typeof setTimeout> | null = null

function handleSearchInput() {
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(() => {
    store.offset = 0
    void store.fetchPayments()
  }, 300)
}

function handleSearch() {
  if (searchTimer) clearTimeout(searchTimer)
  store.offset = 0
  void store.fetchPayments()
}

onBeforeUnmount(() => {
  if (searchTimer) clearTimeout(searchTimer)
})

function handlePageChange(page: number) {
  store.offset = (page - 1) * store.limit
  void store.fetchPayments()
}

function handleRowClick(row: PaymentListItem) {
  router.push(`/invoices/${row.invoice_id}/edit`)
}

const currentPage = computed(() => Math.floor(store.offset / store.limit) + 1)
const pageCount = computed(() => Math.max(1, Math.ceil(store.total / store.limit)))

const fmtMoney = (v: string | number) => Number(v).toFixed(2)

const sortOptions = computed(() => [
  { label: t('payments.sortByPaymentDate'), value: 'payment_date' },
  { label: t('payments.sortByCreatedAt'), value: 'created_at' },
])

function handleSortChange(val: 'payment_date' | 'created_at') {
  store.sortBy = val
  store.offset = 0
  void store.fetchPayments()
}

const columns = computed(() => [
  {
    title: t('payments.paymentDate'),
    key: 'payment_date',
    width: 120,
    render(row: PaymentListItem) {
      return formatDate(row.payment_date)
    },
  },
  {
    title: t('payments.invoiceNumber'),
    key: 'invoice_number',
    render(row: PaymentListItem) {
      return h(
        NText,
        {
          style: 'cursor:pointer; color: var(--n-primary-color)',
          onClick: () => router.push(`/invoices/${row.invoice_id}/edit`),
        },
        () => row.invoice_number ?? t('invoices.concept'),
      )
    },
  },
  {
    title: t('payments.customer'),
    key: 'customer_name',
    ellipsis: { tooltip: true },
  },
  {
    title: t('payments.amount'),
    key: 'amount',
    align: 'right' as const,
    width: 120,
    render(row: PaymentListItem) {
      return fmtMoney(row.amount)
    },
  },
  {
    title: t('payments.paymentMethod'),
    key: 'payment_method_name',
    width: 150,
    render(row: PaymentListItem) {
      return row.payment_method_name ?? h(NText, { depth: 3 }, () => '—')
    },
  },
  {
    title: t('payments.createdAt'),
    key: 'created_at',
    width: 130,
    render(row: PaymentListItem) {
      return formatDate(row.created_at)
    },
  },
  {
    title: '',
    key: 'actions',
    width: 140,
    align: 'center' as const,
    render(row: PaymentListItem) {
      return h(
        NButton,
        {
          size: 'small',
          secondary: true,
          onClick: () => router.push(`/invoices/${row.invoice_id}/edit`),
        },
        () => t('payments.viewInvoice'),
      )
    },
  },
])
</script>

<template>
  <div class="payment-list-page">
    <div class="payment-list-container">
          <div class="payment-list-header">
            <h2>{{ t('payments.title') }}</h2>
            <n-space align="center" wrap>
              <n-input
                v-model:value="store.query"
                :placeholder="t('payments.search')"
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
                :placeholder="t('payments.customerAll')"
                style="width: 160px"
                filterable
                clearable
                @search="searchCustomers"
                @update:value="handleCustomerFilter"
              />
              <n-select
                :value="store.paymentMethodIdFilter"
                :options="paymentMethodOptions"
                :placeholder="t('payments.methodAll')"
                style="width: 150px"
                clearable
                @update:value="handleMethodFilter"
              />
              <n-date-picker
                v-model:value="dateRange"
                type="daterange"
                clearable
                :start-placeholder="t('payments.dateFrom')"
                :end-placeholder="t('payments.dateTo')"
                style="width: 240px"
                @update:value="handleDateRange"
              />
              <n-select
                :value="store.sortBy"
                :options="sortOptions"
                style="width: 140px"
                @update:value="handleSortChange"
              />
            </n-space>
          </div>

          <n-alert v-if="store.error" type="error" style="margin-bottom: 16px">
            {{ store.error }}
          </n-alert>

          <n-spin :show="store.loading">
            <template v-if="!store.loading && store.items.length === 0">
              <n-empty
                v-if="store.total === 0 && !store.query && !store.customerIdFilter && !store.paymentMethodIdFilter"
                :description="t('payments.empty')"
              />
              <n-empty v-else :description="t('payments.noResults')" />
            </template>

            <n-data-table
              v-else
              :columns="columns"
              :data="store.items"
              :bordered="false"
              :row-key="(row: PaymentListItem) => row.id"
              :row-props="(row: PaymentListItem) => ({ style: 'cursor:pointer', onClick: () => handleRowClick(row) })"
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
  </div>
</template>

<style scoped>
.payment-list-container {
  max-width: 1200px;
  margin: 0 auto;
  padding: 24px;
}

.payment-list-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 16px;
  flex-wrap: wrap;
  gap: 12px;
}

.payment-list-header h2 {
  margin: 0;
}

.pagination-container {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}
</style>
