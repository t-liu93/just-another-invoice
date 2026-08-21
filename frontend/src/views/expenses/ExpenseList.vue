<script setup lang="ts">
import { onMounted, onBeforeUnmount, computed, h, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import {
  NButton, NSpace, NInput, NDataTable, NAlert, NSpin,
  NPagination, NSelect, NDatePicker, NTag, NText, NPopconfirm, NTabs, NTabPane,
  useMessage,
} from 'naive-ui'
import { SearchOutline, AddOutline, CreateOutline, TrashOutline } from '@vicons/ionicons5'
import { NIcon } from 'naive-ui'
import { useExpensesStore } from '../../stores/expenses'
import type { ExpenseListItem } from '../../stores/expenses'
import { useMileageStore, type MileageExpenseListItem } from '../../stores/mileage'
import { get } from '../../api/http'
import type { components } from '../../api/schema'
import { localDateStr, localDateTimestamp, formatDate } from '../../utils/date'

type ExpenseCategoryRead = components['schemas']['ExpenseCategoryRead']
type ExpenseCategoryListResponse = components['schemas']['ExpenseCategoryListResponse']
type MileageTransportTypeRead = components['schemas']['MileageTransportTypeRead']
type MileageTransportTypeListResponse = components['schemas']['MileageTransportTypeListResponse']

const router = useRouter()
const route = useRoute()
const { t } = useI18n()
const store = useExpensesStore()
const mileageStore = useMileageStore()
const message = useMessage()

const categories = ref<ExpenseCategoryRead[]>([])
const mileageTypes = ref<MileageTransportTypeRead[]>([])

async function loadCategories() {
  const res = await get<ExpenseCategoryListResponse>('/api/v1/expense-categories')
  categories.value = res.items
}

async function loadMileageTypes() {
  const res = await get<MileageTransportTypeListResponse>('/api/v1/mileage-transport-types')
  mileageTypes.value = res.items
}

const categoryOptions = computed(() =>
  categories.value.map(c => ({ label: c.name, value: c.id }))
)

const deductibleOptions = computed(() => [
  { label: t('expenses.deductibleYes'), value: 'true' },
  { label: t('expenses.deductibleNo'), value: 'false' },
])

const draftOptions = computed(() => [
  { label: t('expenses.draftOnly'), value: 'true' },
  { label: t('expenses.confirmedOnly'), value: 'false' },
])

const sortOptions = computed(() => [
  { label: t('expenses.sortByDate'), value: 'expense_date' },
  { label: t('expenses.sortByCreatedAt'), value: 'created_at' },
])

const mileageTypeOptions = computed(() => mileageTypes.value.filter(type => type.active).map(type => ({ label: type.name, value: type.id })))
const mileageSortOptions = computed(() => [
  { label: t('mileage.sortByTripDate'), value: 'trip_date' },
  { label: t('mileage.sortByCreatedAt'), value: 'created_at' },
])

function handleCategoryFilter(val: string | null) {
  store.categoryIdFilter = val
  store.offset = 0
  void store.fetchExpenses('PURCHASE')
}

function handleDeductibleFilter(val: string | null) {
  store.deductibleFilter = val === null ? null : val === 'true'
  store.offset = 0
  void store.fetchExpenses('PURCHASE')
}

function handleDraftFilter(val: string | null) {
  store.isDraftFilter = val === null ? null : val === 'true'
  store.offset = 0
  void store.fetchExpenses('PURCHASE')
}

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
  void store.fetchExpenses('PURCHASE')
}

function handleSortChange(val: 'expense_date' | 'created_at') {
  store.sortBy = val
  store.offset = 0
  void store.fetchExpenses('PURCHASE')
}

const activeTab = computed({
  get: () => route.query.tab === 'mileage' ? 'mileage' : 'purchase',
  set: (tab: string) => void router.replace({ query: { ...route.query, tab: tab === 'mileage' ? 'mileage' : 'purchase' } }),
})

function fetchActiveTab() {
  if (activeTab.value === 'mileage') void mileageStore.fetchMileage()
  else void store.fetchExpenses('PURCHASE')
}

watch(activeTab, fetchActiveTab)

onMounted(() => {
  // Keep old links working, but make the selected tab explicit for reloads and
  // editor/list navigation.  The computed tab does not change while replacing
  // an absent or invalid value with purchase, so the watcher cannot refetch.
  if (route.query.tab !== activeTab.value) {
    void router.replace({ query: { ...route.query, tab: activeTab.value } })
  }
  fetchActiveTab()
  void loadCategories()
  void loadMileageTypes()
})

let searchTimer: ReturnType<typeof setTimeout> | null = null
let mileageSearchTimer: ReturnType<typeof setTimeout> | null = null

function handleSearchInput() {
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(() => {
    store.offset = 0
    void store.fetchExpenses('PURCHASE')
  }, 300)
}

function handleSearch() {
  if (searchTimer) clearTimeout(searchTimer)
  store.offset = 0
  void store.fetchExpenses('PURCHASE')
}

function fetchMileageFromFirstPage() {
  mileageStore.offset = 0
  void mileageStore.fetchMileage()
}

function handleMileageSearchInput() {
  if (mileageSearchTimer) clearTimeout(mileageSearchTimer)
  mileageSearchTimer = setTimeout(fetchMileageFromFirstPage, 300)
}

function handleMileageSearch() {
  if (mileageSearchTimer) clearTimeout(mileageSearchTimer)
  fetchMileageFromFirstPage()
}

function handleMileageTypeFilter(value: string | null) {
  mileageStore.transportTypeId = value
  fetchMileageFromFirstPage()
}

function handleMileageDateRange(value: [number, number] | null) {
  if (value) {
    mileageStore.dateFrom = localDateStr(new Date(value[0]))
    mileageStore.dateTo = localDateStr(new Date(value[1]))
  } else {
    mileageStore.dateFrom = null
    mileageStore.dateTo = null
  }
  fetchMileageFromFirstPage()
}

const mileageDateRange = computed<[number, number] | null>({
  get: () => {
    const dateFrom = localDateTimestamp(mileageStore.dateFrom)
    const dateTo = localDateTimestamp(mileageStore.dateTo)
    return dateFrom !== null && dateTo !== null ? [dateFrom, dateTo] as [number, number] : null
  },
  set: handleMileageDateRange,
})

function handleMileageSortChange(value: 'trip_date' | 'created_at') {
  mileageStore.sortBy = value
  fetchMileageFromFirstPage()
}

onBeforeUnmount(() => {
  if (searchTimer) clearTimeout(searchTimer)
  if (mileageSearchTimer) clearTimeout(mileageSearchTimer)
  mileageStore.cancelMileageFetch()
})

function handlePageChange(page: number) {
  store.offset = (page - 1) * store.limit
  void store.fetchExpenses('PURCHASE')
}

const currentPage = computed(() => Math.floor(store.offset / store.limit) + 1)
const pageCount = computed(() => Math.max(1, Math.ceil(store.total / store.limit)))

const fmtMoney = (v: string | number) => Number(v).toFixed(2)

const confirmingId = ref<string | null>(null)
const deletingId = ref<string | null>(null)

async function handleConfirm(row: ExpenseListItem) {
  confirmingId.value = row.id
  try {
    const full = await store.getExpense(row.id)
    // Guard: if the category was deleted (category_id is null), a direct confirm
    // would send an empty-string UUID → 422.  Direct the user to edit and re-select.
    if (!full.category_id) {
      message.warning(t('expenses.confirmNeedsCategoryEdit'))
      void router.push({ path: `/expenses/${row.id}/edit`, query: { tab: 'purchase' } })
      return
    }
    await store.confirmDraft(row.id, full)
    void store.fetchExpenses('PURCHASE')
  } catch {
    // error already in store
  } finally {
    confirmingId.value = null
  }
}

async function handleDelete(id: string) {
  deletingId.value = id
  try {
    await store.deleteExpense(id)
    void store.fetchExpenses('PURCHASE')
  } catch {
    // error already in store
  } finally {
    deletingId.value = null
  }
}

const columns = computed(() => [
  {
    title: t('expenses.expenseDate'),
    key: 'expense_date',
    width: 120,
    render(row: ExpenseListItem) {
      return formatDate(row.expense_date)
    },
  },
  {
    title: t('expenses.category'),
    key: 'category_name',
    ellipsis: { tooltip: true },
    render(row: ExpenseListItem) {
      return row.category_name ?? h(NText, { depth: 3 }, () => '—')
    },
  },
  {
    title: t('expenses.supplier'),
    key: 'supplier_name',
    ellipsis: { tooltip: true },
    render(row: ExpenseListItem) {
      return row.supplier_name ?? h(NText, { depth: 3 }, () => '—')
    },
  },
  {
    title: t('expenses.netAmount'),
    key: 'net_amount',
    align: 'right' as const,
    width: 110,
    render(row: ExpenseListItem) {
      return fmtMoney(row.net_amount)
    },
  },
  {
    title: t('expenses.vatAmount'),
    key: 'vat_amount',
    align: 'right' as const,
    width: 100,
    render(row: ExpenseListItem) {
      return fmtMoney(row.vat_amount)
    },
  },
  {
    title: t('expenses.grossAmount'),
    key: 'gross_amount',
    align: 'right' as const,
    width: 110,
    render(row: ExpenseListItem) {
      return fmtMoney(row.gross_amount)
    },
  },
  {
    title: t('expenses.deductible'),
    key: 'deductible',
    width: 90,
    align: 'center' as const,
    render(row: ExpenseListItem) {
      return row.deductible
        ? h(NTag, { type: 'success', size: 'small' }, () => t('vat.yes'))
        : h(NTag, { type: 'default', size: 'small' }, () => t('vat.no'))
    },
  },
  {
    title: t('expenses.paidBy'),
    key: 'paid_by',
    width: 100,
    align: 'center' as const,
    render(row: ExpenseListItem) {
      return row.paid_by === 'PRIVATE'
        ? h(NTag, { type: 'warning', size: 'small' }, () => t('expenses.paidByPrivate'))
        : h(NTag, { type: 'default', size: 'small' }, () => t('expenses.paidByBusiness'))
    },
  },
  {
    title: t('expenses.bookkeeping'),
    key: 'bookkeeping',
    width: 120,
    align: 'center' as const,
    render(row: ExpenseListItem) {
      const tags: ReturnType<typeof h>[] = []
      const pct = Number(row.business_percentage)
      if (pct !== 100) {
        tags.push(h(NTag, { type: 'info', size: 'small', style: 'margin: 1px' }, () => `${pct.toFixed(0)}%`))
      }
      if (row.depreciation_years > 1) {
        tags.push(h(NTag, { type: 'info', size: 'small', style: 'margin: 1px' }, () => `${row.depreciation_years}y`))
      }
      return tags.length ? h(NSpace, { size: 2, align: 'center', wrap: true }, () => tags) : h(NText, { depth: 3 }, () => '—')
    },
  },
  {
    title: t('expenses.status'),
    key: 'is_draft',
    width: 90,
    align: 'center' as const,
    render(row: ExpenseListItem) {
      return row.is_draft
        ? h(NTag, { type: 'warning', size: 'small' }, () => t('expenses.draft'))
        : h(NTag, { type: 'info', size: 'small' }, () => t('expenses.confirmed'))
    },
  },
  {
    title: '',
    key: 'actions',
    width: 170,
    align: 'center' as const,
    render(row: ExpenseListItem) {
      const editBtn = h(
        NButton,
        {
          size: 'small',
          quaternary: true,
          circle: true,
          title: t('expenses.editAction'),
          'aria-label': t('expenses.editAction'),
          onClick: (e: MouseEvent) => { e.stopPropagation(); void router.push({ path: `/expenses/${row.id}/edit`, query: { tab: 'purchase' } }) },
        },
        () => h(NIcon, null, { default: () => h(CreateOutline) }),
      )

      // Prod-build safe: separate v-if/v-else pattern for dynamic loading buttons
      const confirmBtn = row.is_draft
        ? (confirmingId.value === row.id
          ? h(NButton, { size: 'small', type: 'primary', loading: true }, () => t('expenses.confirm'))
          : h(NButton, {
              size: 'small',
              type: 'primary',
              onClick: (e: MouseEvent) => { e.stopPropagation(); void handleConfirm(row) },
            }, () => t('expenses.confirm')))
        : null

      const deleteBtn = h(
        NPopconfirm,
        { onPositiveClick: () => void handleDelete(row.id) },
        {
          trigger: () => (
            deletingId.value === row.id
              ? h(NButton, {
                  size: 'small',
                  quaternary: true,
                  circle: true,
                  type: 'error',
                  title: t('expenses.delete'),
                  'aria-label': t('expenses.delete'),
                  loading: true,
                  onClick: (e: MouseEvent) => e.stopPropagation(),
                }, () => h(NIcon, null, { default: () => h(TrashOutline) }))
              : h(NButton, {
                  size: 'small',
                  quaternary: true,
                  circle: true,
                  type: 'error',
                  title: t('expenses.delete'),
                  'aria-label': t('expenses.delete'),
                  onClick: (e: MouseEvent) => e.stopPropagation(),
                }, () => h(NIcon, null, { default: () => h(TrashOutline) }))
          ),
          default: () => t('expenses.deleteConfirm'),
        },
      )

      const btns = [editBtn]
      if (confirmBtn) btns.push(confirmBtn)
      btns.push(deleteBtn)

      return h(NSpace, { size: 4, justify: 'center', wrapItem: false }, () => btns)
    },
  },
])

const mileageDeletingId = ref<string | null>(null)

async function deleteMileage(row: MileageExpenseListItem) {
  mileageDeletingId.value = row.id
  try {
    await mileageStore.deleteMileage(row.id)
    // A deletion can leave the current page beyond the last valid offset.
    mileageStore.clampOffsetForTotal(Math.max(0, mileageStore.total - 1))
    await mileageStore.fetchMileage()
  } finally { mileageDeletingId.value = null }
}

function routeSummary(row: MileageExpenseListItem) {
  if (row.origin_address && row.destination_address) return `${row.origin_address} → ${row.destination_address}`
  return row.origin_address ?? row.destination_address ?? '—'
}

const mileageColumns = computed(() => [
  { title: t('mileage.date'), key: 'trip_date', width: 110, render: (row: MileageExpenseListItem) => formatDate(row.trip_date) },
  { title: t('mileage.type'), key: 'transport_type_name', width: 130, render: (row: MileageExpenseListItem) => row.transport_type_name },
  { title: t('mileage.oneWay'), key: 'one_way_distance_km', width: 100, align: 'right' as const, render: (row: MileageExpenseListItem) => `${row.one_way_distance_km} km` },
  { title: t('mileage.return'), key: 'round_trip', width: 80, align: 'center' as const, render: (row: MileageExpenseListItem) => row.round_trip ? t('mileage.yes') : t('mileage.no') },
  { title: t('mileage.totalDistance'), key: 'total_distance_km', width: 100, align: 'right' as const, render: (row: MileageExpenseListItem) => `${row.total_distance_km} km` },
  { title: t('mileage.rate'), key: 'rate_per_km', width: 90, align: 'right' as const, render: (row: MileageExpenseListItem) => row.rate_per_km },
  { title: t('mileage.amount'), key: 'amount', width: 100, align: 'right' as const, render: (row: MileageExpenseListItem) => row.amount },
  { title: t('mileage.purpose'), key: 'purpose', ellipsis: { tooltip: true }, render: (row: MileageExpenseListItem) => row.purpose ?? '—' },
  { title: t('mileage.route'), key: 'route', ellipsis: { tooltip: true }, render: routeSummary },
  { title: t('mileage.actions'), key: 'actions', width: 96, align: 'center' as const, render: (row: MileageExpenseListItem) => h(NSpace, { size: 4, justify: 'center', wrapItem: false }, () => [
    h(NButton, {
      size: 'small', quaternary: true, circle: true,
      title: t('expenses.editAction'), 'aria-label': t('expenses.editAction'),
      onClick: () => void router.push({ path: `/expenses/mileage/${row.id}/edit`, query: { tab: 'mileage' } }),
    }, () => h(NIcon, null, { default: () => h(CreateOutline) })),
    h(NPopconfirm, { onPositiveClick: () => void deleteMileage(row) }, { trigger: () => h(NButton, {
      size: 'small', quaternary: true, circle: true, type: 'error',
      title: t('expenses.delete'), 'aria-label': t('expenses.delete'),
      loading: mileageDeletingId.value === row.id,
    }, () => h(NIcon, null, { default: () => h(TrashOutline) })), default: () => t('mileage.deleteConfirm') }),
  ]) },
])

function mileagePageChange(page: number) {
  mileageStore.offset = (page - 1) * mileageStore.limit
  void mileageStore.fetchMileage()
}

const mileageCurrentPage = computed(() => Math.floor(mileageStore.offset / mileageStore.limit) + 1)
const mileagePageCount = computed(() => Math.max(1, Math.ceil(mileageStore.total / mileageStore.limit)))
</script>

<template>
  <div class="expense-list-page">
    <div class="expense-list-container">
      <h2>{{ t('expenses.title') }}</h2>
      <n-tabs v-model:value="activeTab" type="line" animated>
        <n-tab-pane name="purchase" :tab="t('mileage.purchaseTab')">
          <div class="expense-list-header">
            <n-space align="center" wrap>
              <n-input
                v-model:value="store.query"
                :placeholder="t('expenses.search')"
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
                :value="store.categoryIdFilter"
                :options="categoryOptions"
                :placeholder="t('expenses.categoryAll')"
                style="width: 160px"
                clearable
                @update:value="handleCategoryFilter"
              />
              <n-select
                :value="store.deductibleFilter === null ? null : String(store.deductibleFilter)"
                :options="deductibleOptions"
                :placeholder="t('expenses.deductibleAll')"
                style="width: 130px"
                clearable
                @update:value="handleDeductibleFilter"
              />
              <n-select
                :value="store.isDraftFilter === null ? null : String(store.isDraftFilter)"
                :options="draftOptions"
                :placeholder="t('expenses.draftAll')"
                style="width: 120px"
                clearable
                @update:value="handleDraftFilter"
              />
              <n-date-picker
                v-model:value="dateRange"
                type="daterange"
                clearable
                :start-placeholder="t('expenses.dateFrom')"
                :end-placeholder="t('expenses.dateTo')"
                style="width: 240px"
                @update:value="handleDateRange"
              />
              <n-select
                :value="store.sortBy"
                :options="sortOptions"
                style="width: 140px"
                @update:value="handleSortChange"
              />
              <n-button type="primary" @click="router.push({ path: '/expenses/new', query: { tab: 'purchase' } })">
                <template #icon>
                  <n-icon><AddOutline /></n-icon>
                </template>
                {{ t('expenses.new') }}
              </n-button>
            </n-space>
          </div>

          <n-alert v-if="store.error" type="error" style="margin-bottom: 16px">
            {{ store.error }}
          </n-alert>

          <n-spin :show="store.loading">
            <template v-if="!store.loading && store.items.length === 0">
              <n-empty
                v-if="store.total === 0 && !store.query && !store.categoryIdFilter"
                :description="t('expenses.empty')"
              />
              <n-empty v-else :description="t('expenses.noResults')" />
            </template>

            <n-data-table
              v-else
              :columns="columns"
              :data="store.items"
              :bordered="false"
              :row-key="(row: ExpenseListItem) => row.id"
              :row-props="(row: ExpenseListItem) => ({ style: 'cursor:pointer', onClick: () => router.push({ path: `/expenses/${row.id}/edit`, query: { tab: 'purchase' } }) })"
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
        </n-tab-pane>
        <n-tab-pane name="mileage" :tab="t('mileage.mileageTab')">
          <div class="expense-list-header">
            <n-space align="center" wrap>
              <n-input v-model:value="mileageStore.query" :placeholder="t('mileage.search')" clearable style="width: 220px" @input="handleMileageSearchInput" @keyup.enter="handleMileageSearch" @clear="handleMileageSearch"><template #prefix><n-icon><SearchOutline /></n-icon></template></n-input>
              <n-select :value="mileageStore.transportTypeId" :options="mileageTypeOptions" :placeholder="t('mileage.allTypes')" clearable style="width: 180px" @update:value="handleMileageTypeFilter" />
              <n-date-picker v-model:value="mileageDateRange" type="daterange" clearable :start-placeholder="t('expenses.dateFrom')" :end-placeholder="t('expenses.dateTo')" style="width: 240px" />
              <n-select :value="mileageStore.sortBy" :options="mileageSortOptions" style="width: 160px" @update:value="handleMileageSortChange" />
            </n-space>
            <n-button type="primary" @click="router.push({ path: '/expenses/mileage/new', query: { tab: 'mileage' } })"><template #icon><n-icon><AddOutline /></n-icon></template>{{ t('mileage.new') }}</n-button>
          </div>
          <n-alert v-if="mileageStore.error" type="error" style="margin-bottom: 16px">{{ mileageStore.error }}</n-alert>
          <n-spin :show="mileageStore.loading">
            <n-empty v-if="!mileageStore.loading && mileageStore.items.length === 0" :description="mileageStore.total === 0 ? t('mileage.empty') : t('mileage.noResults')" />
            <n-data-table v-else :columns="mileageColumns" :data="mileageStore.items" :bordered="false" :row-key="(row: MileageExpenseListItem) => row.id" striped />
            <div v-if="mileageStore.total > mileageStore.limit" class="pagination-container"><n-pagination :page="mileageCurrentPage" :page-count="mileagePageCount" @update:page="mileagePageChange" /></div>
          </n-spin>
        </n-tab-pane>
      </n-tabs>
    </div>
  </div>
</template>

<style scoped>

.expense-list-container {
  max-width: 1300px;
  margin: 0 auto;
  padding: 24px;
}

.expense-list-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 16px;
  flex-wrap: wrap;
  gap: 12px;
}

.expense-list-header h2 {
  margin: 0;
}

.pagination-container {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}
</style>
