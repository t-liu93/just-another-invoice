<script setup lang="ts">
import { onMounted, onBeforeUnmount, computed, h } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import {
  useMessage, useDialog,
  NButton, NSpace, NInput, NDataTable, NAlert, NSpin,
  NPagination, NSelect, NTag,
} from 'naive-ui'
import { AddOutline, SearchOutline, CreateOutline, TrashOutline } from '@vicons/ionicons5'
import { NIcon } from 'naive-ui'
import AppHeader from '../../components/AppHeader.vue'
import { useInvoicesStore } from '../../stores/invoices'
import type { InvoiceListItem } from '../../stores/invoices'

const router = useRouter()
const { t } = useI18n()
const message = useMessage()
const dialog = useDialog()
const store = useInvoicesStore()

onMounted(() => {
  store.fetchInvoices()
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

function statusTagType(status: string): 'default' | 'info' | 'success' | 'warning' | 'error' {
  const map: Record<string, 'default' | 'info' | 'success' | 'warning' | 'error'> = {
    DRAFT: 'default',
    SENT: 'info',
    COMPLETED: 'success',
    CANCELLED: 'warning',
  }
  return map[status] ?? 'default'
}

const columns = computed(() => [
  {
    title: t('invoices.invoiceNumber'),
    key: 'invoice_number',
    width: 140,
  },
  {
    title: t('invoices.invoiceDate'),
    key: 'invoice_date',
    width: 120,
  },
  {
    title: t('invoices.status'),
    key: 'status',
    width: 110,
    render(row: InvoiceListItem) {
      return h(NTag, { type: statusTagType(row.status), size: 'small' }, () => t(`invoices.status${row.status}`))
    },
  },
  {
    title: t('invoices.currency'),
    key: 'currency',
    width: 80,
  },
  {
    title: t('invoices.totalInclVat'),
    key: 'total_incl_vat',
    align: 'right' as const,
    render(row: InvoiceListItem) {
      return h('span', {}, `${Number(row.total_incl_vat).toFixed(2)}`)
    },
  },
  {
    title: t('invoices.actions'),
    key: 'actions',
    width: 96,
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
        ...(isEditable ? [h(
          NButton,
          {
            size: 'small',
            quaternary: true,
            circle: true,
            type: 'error',
            title: t('invoices.delete'),
            onClick: () => handleDelete(row.invoice_number, row.id),
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
    <n-layout>
      <AppHeader />

      <n-layout-content class="app-content">
        <div class="invoice-list-container">
          <div class="invoice-list-header">
            <h2>{{ t('invoices.title') }}</h2>
            <n-space align="center">
              <n-input
                v-model:value="store.query"
                :placeholder="t('invoices.search')"
                clearable
                style="width: 220px"
                @input="handleSearchInput"
                @keyup.enter="handleSearch"
                @clear="handleSearch"
              >
                <template #prefix>
                  <n-icon><SearchOutline /></n-icon>
                </template>
              </n-input>
              <n-select
                :value="store.statusFilter"
                :options="statusOptions"
                :placeholder="t('invoices.statusAll')"
                style="width: 130px"
                clearable
                @update:value="handleStatusFilter"
              />
              <n-select
                :value="store.sortBy"
                :options="sortOptions"
                style="width: 150px"
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
                v-if="store.total === 0 && !store.query && !store.statusFilter"
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
      </n-layout-content>
    </n-layout>
  </div>
</template>

<style scoped>
.app-content {
  min-height: calc(100vh - 57px);
}

.invoice-list-container {
  max-width: 1080px;
  margin: 0 auto;
  padding: 24px;
}

.invoice-list-header {
  display: flex;
  align-items: center;
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
