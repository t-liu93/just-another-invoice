<script setup lang="ts">
import { onMounted, onBeforeUnmount, computed, h, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import {
  NButton, NSpace, NInput, NDataTable, NAlert, NSpin,
  NPagination, NSelect, NDatePicker, NTag, NText, NPopconfirm,
  useMessage,
} from 'naive-ui'
import { SearchOutline, AddOutline } from '@vicons/ionicons5'
import { NIcon } from 'naive-ui'
import { useExpensesStore } from '../../stores/expenses'
import type { ExpenseListItem } from '../../stores/expenses'
import { get } from '../../api/http'
import type { components } from '../../api/schema'
import { localDateStr } from '../../utils/date'

type ExpenseCategoryRead = components['schemas']['ExpenseCategoryRead']
type ExpenseCategoryListResponse = components['schemas']['ExpenseCategoryListResponse']

const router = useRouter()
const { t } = useI18n()
const store = useExpensesStore()
const message = useMessage()

const categories = ref<ExpenseCategoryRead[]>([])

async function loadCategories() {
  const res = await get<ExpenseCategoryListResponse>('/api/v1/expense-categories')
  categories.value = res.items
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

function handleCategoryFilter(val: string | null) {
  store.categoryIdFilter = val
  store.offset = 0
  void store.fetchExpenses()
}

function handleDeductibleFilter(val: string | null) {
  store.deductibleFilter = val === null ? null : val === 'true'
  store.offset = 0
  void store.fetchExpenses()
}

function handleDraftFilter(val: string | null) {
  store.isDraftFilter = val === null ? null : val === 'true'
  store.offset = 0
  void store.fetchExpenses()
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
  void store.fetchExpenses()
}

function handleSortChange(val: 'expense_date' | 'created_at') {
  store.sortBy = val
  store.offset = 0
  void store.fetchExpenses()
}

onMounted(() => {
  void store.fetchExpenses()
  void loadCategories()
})

let searchTimer: ReturnType<typeof setTimeout> | null = null

function handleSearchInput() {
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(() => {
    store.offset = 0
    void store.fetchExpenses()
  }, 300)
}

function handleSearch() {
  if (searchTimer) clearTimeout(searchTimer)
  store.offset = 0
  void store.fetchExpenses()
}

onBeforeUnmount(() => {
  if (searchTimer) clearTimeout(searchTimer)
})

function handlePageChange(page: number) {
  store.offset = (page - 1) * store.limit
  void store.fetchExpenses()
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
      router.push(`/expenses/${row.id}/edit`)
      return
    }
    await store.confirmDraft(row.id, full)
    void store.fetchExpenses()
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
    void store.fetchExpenses()
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
      return new Date(row.expense_date).toLocaleDateString()
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
    width: 210,
    align: 'center' as const,
    render(row: ExpenseListItem) {
      const editBtn = h(
        NButton,
        {
          size: 'small',
          secondary: true,
          onClick: (e: MouseEvent) => { e.stopPropagation(); router.push(`/expenses/${row.id}/edit`) },
        },
        () => t('expenses.editAction'),
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
              ? h(NButton, { size: 'small', type: 'error', loading: true }, () => t('expenses.delete'))
              : h(NButton, {
                  size: 'small',
                  type: 'error',
                  onClick: (e: MouseEvent) => e.stopPropagation(),
                }, () => t('expenses.delete'))
          ),
          default: () => t('expenses.deleteConfirm'),
        },
      )

      const btns = [editBtn]
      if (confirmBtn) btns.push(confirmBtn)
      btns.push(deleteBtn)

      return h(NSpace, { size: 'small' }, () => btns)
    },
  },
])
</script>

<template>
  <div class="expense-list-page">
    <div class="expense-list-container">
          <div class="expense-list-header">
            <h2>{{ t('expenses.title') }}</h2>
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
              <n-button type="primary" @click="router.push('/expenses/new')">
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
              :row-props="(row: ExpenseListItem) => ({ style: 'cursor:pointer', onClick: () => router.push(`/expenses/${row.id}/edit`) })"
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
