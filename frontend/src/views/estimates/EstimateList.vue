<script setup lang="ts">
import { onMounted, onBeforeUnmount, computed, h, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import {
  useMessage, useDialog,
  NButton, NSpace, NInput, NDataTable, NAlert, NSpin,
  NPagination, NSelect, NTag, NText,
} from 'naive-ui'
import { AddOutline, SearchOutline, CreateOutline, TrashOutline } from '@vicons/ionicons5'
import { NIcon } from 'naive-ui'
import { useEstimatesStore } from '../../stores/estimates'
import type { EstimateListItem } from '../../stores/estimates'
import { get } from '../../api/http'
import type { components } from '../../api/schema'
import { formatDate } from '../../utils/date'

type CustomerRead = components['schemas']['CustomerRead']

const router = useRouter()
const { t } = useI18n()
const message = useMessage()
const dialog = useDialog()
const store = useEstimatesStore()

const customers = ref<CustomerRead[]>([])

async function searchCustomers(q: string) {
  const res = await get<{ items: CustomerRead[] }>(`/api/v1/customers?q=${encodeURIComponent(q)}&limit=30`)
  customers.value = res.items
}

const customerOptions = computed(() => customers.value.map(c => ({ label: c.name, value: c.id })))

function handleCustomerFilter(val: string | null) {
  store.customerIdFilter = val
  store.offset = 0
  void store.fetchEstimates()
}

onMounted(() => {
  void store.fetchEstimates()
  void searchCustomers('')
})

let searchTimer: ReturnType<typeof setTimeout> | null = null

function handleSearchInput() {
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(() => {
    store.offset = 0
    void store.fetchEstimates()
  }, 300)
}

function handleSearch() {
  if (searchTimer) clearTimeout(searchTimer)
  store.offset = 0
  void store.fetchEstimates()
}

onBeforeUnmount(() => {
  if (searchTimer) clearTimeout(searchTimer)
})

function handlePageChange(page: number) {
  store.offset = (page - 1) * store.limit
  void store.fetchEstimates()
}

function handleCreate() {
  router.push('/estimates/new')
}

function handleEdit(id: string) {
  router.push(`/estimates/${id}/edit`)
}

function handleDelete(name: string, id: string) {
  dialog.warning({
    title: t('estimates.delete'),
    content: t('estimates.deleteConfirm', { name }),
    positiveText: t('estimates.delete'),
    negativeText: t('common.cancel'),
    onPositiveClick: async () => {
      try {
        await store.deleteEstimate(id)
        message.success(t('estimates.deleteSuccess'))
        void store.fetchEstimates()
      } catch {
        message.error(t('estimates.deleteFailed'))
      }
    },
  })
}

const currentPage = computed(() => Math.floor(store.offset / store.limit) + 1)
const pageCount = computed(() => Math.max(1, Math.ceil(store.total / store.limit)))

const sortOptions = computed(() => [
  { label: t('estimates.sortByUpdatedAt'), value: 'updated_at' },
  { label: t('estimates.sortByCreatedAt'), value: 'created_at' },
  { label: t('estimates.sortByName'), value: 'name' },
])

function handleSortChange(val: 'name' | 'created_at' | 'updated_at') {
  store.sortBy = val
  store.offset = 0
  void store.fetchEstimates()
}

const fmtMoney = (v: string | number) => Number(v).toFixed(2)

const columns = computed(() => [
  {
    title: t('estimates.name'),
    key: 'name',
    ellipsis: { tooltip: true },
  },
  {
    title: t('estimates.customer'),
    key: 'customer_name',
    ellipsis: { tooltip: true },
    render(row: EstimateListItem) {
      return row.customer_name ?? h(NText, { depth: 3 }, () => '—')
    },
  },
  {
    title: t('estimates.totalExclVatCol'),
    key: 'total_excl_vat',
    align: 'right' as const,
    width: 140,
    render(row: EstimateListItem) {
      return fmtMoney(row.total_excl_vat)
    },
  },
  {
    title: t('estimates.totalMarginCol'),
    key: 'total_margin',
    align: 'right' as const,
    width: 120,
    render(row: EstimateListItem) {
      return fmtMoney(row.total_margin)
    },
  },
  {
    title: t('estimates.generatedQuoteLink'),
    key: 'generated_quote_id',
    width: 130,
    render(row: EstimateListItem) {
      if (!row.generated_quote_id) return h(NText, { depth: 3 }, () => '—')
      return h(
        NTag,
        {
          type: 'success',
          size: 'small',
          style: 'cursor:pointer',
          onClick: () => router.push(`/quotes/${row.generated_quote_id}/edit`),
        },
        () => t('estimates.viewQuote'),
      )
    },
  },
  {
    title: t('estimates.updatedAt'),
    key: 'updated_at',
    width: 130,
    render(row: EstimateListItem) {
      return formatDate(row.updated_at)
    },
  },
  {
    title: t('estimates.actions'),
    key: 'actions',
    width: 88,
    align: 'center' as const,
    render(row: EstimateListItem) {
      return h(NSpace, { size: 4, justify: 'center', wrapItem: false }, () => [
        h(
          NButton,
          {
            size: 'small',
            quaternary: true,
            circle: true,
            title: t('estimates.edit'),
            onClick: () => handleEdit(row.id),
          },
          () => h(NIcon, null, { default: () => h(CreateOutline) }),
        ),
        h(
          NButton,
          {
            size: 'small',
            quaternary: true,
            circle: true,
            type: 'error',
            title: t('estimates.delete'),
            onClick: () => handleDelete(row.name, row.id),
          },
          () => h(NIcon, null, { default: () => h(TrashOutline) }),
        ),
      ])
    },
  },
])
</script>

<template>
  <div class="estimate-list-page">
    <div class="estimate-list-container">
          <div class="estimate-list-header">
            <h2>{{ t('estimates.title') }}</h2>
            <n-space align="center" wrap>
              <n-input
                v-model:value="store.query"
                :placeholder="t('estimates.search')"
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
                :placeholder="t('estimates.customerAll')"
                style="width: 160px"
                filterable
                clearable
                @search="searchCustomers"
                @update:value="handleCustomerFilter"
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
                {{ t('estimates.create') }}
              </n-button>
            </n-space>
          </div>

          <n-alert v-if="store.error" type="error" style="margin-bottom: 16px">
            {{ store.error }}
          </n-alert>

          <n-spin :show="store.loading">
            <template v-if="!store.loading && store.items.length === 0">
              <n-empty
                v-if="store.total === 0 && !store.query && !store.customerIdFilter"
                :description="t('estimates.empty')"
              />
              <n-empty v-else :description="t('estimates.noResults')" />
            </template>

            <n-data-table
              v-else
              :columns="columns"
              :data="store.items"
              :bordered="false"
              :row-key="(row: EstimateListItem) => row.id"
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
.estimate-list-container {
  max-width: 1200px;
  margin: 0 auto;
  padding: 24px;
}

.estimate-list-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 16px;
  flex-wrap: wrap;
  gap: 12px;
}

.estimate-list-header h2 {
  margin: 0;
}

.pagination-container {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}
</style>
