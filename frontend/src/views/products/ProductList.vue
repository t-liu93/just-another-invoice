<script setup lang="ts">
/**
 * Product catalogue list page – search, category filter, pagination, sort.
 * Internal cost/margin fields shown only to the owner (no customer-facing outputs).
 */
import { onMounted, onBeforeUnmount, computed, h } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import {
  useMessage, useDialog,
  NButton, NSpace, NInput, NDataTable, NAlert, NSpin,
  NPagination, NSelect, NTag, type SelectOption,
} from 'naive-ui'
import { AddOutline, SearchOutline, CreateOutline, TrashOutline } from '@vicons/ionicons5'
import { NIcon } from 'naive-ui'
import AppHeader from '../../components/AppHeader.vue'
import { useProductsStore } from '../../stores/products'

const router = useRouter()
const { t } = useI18n()
const message = useMessage()
const dialog = useDialog()
const store = useProductsStore()

onMounted(async () => {
  await store.fetchCategories()
  await store.fetchProducts()
})

let searchTimer: ReturnType<typeof setTimeout> | null = null

function handleSearchInput() {
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(() => {
    store.offset = 0
    store.fetchProducts()
  }, 300)
}

function handleSearch() {
  if (searchTimer) clearTimeout(searchTimer)
  store.offset = 0
  store.fetchProducts()
}

onBeforeUnmount(() => {
  if (searchTimer) clearTimeout(searchTimer)
})

function handlePageChange(page: number) {
  store.offset = (page - 1) * store.limit
  store.fetchProducts()
}

function handleCategoryChange(val: string | null) {
  store.categoryFilter = val
  store.offset = 0
  store.fetchProducts()
}

function handleSortChange(val: 'name' | 'created_at') {
  store.sortBy = val
  store.offset = 0
  store.fetchProducts()
}

const categoryOptions = computed<SelectOption[]>(() => [
  { label: t('products.allCategories'), value: null as unknown as string },
  ...store.categories.map((c) => ({ label: c.name, value: c.id })),
])

const sortOptions = computed(() => [
  { label: t('products.sortByCreatedAt'), value: 'created_at' },
  { label: t('products.sortByName'), value: 'name' },
])

function handleCreate() {
  router.push('/products/new')
}

function handleEdit(id: string) {
  router.push(`/products/${id}/edit`)
}

function handleDelete(_name: string, id: string) {
  dialog.warning({
    title: t('products.delete'),
    content: t('products.deleteConfirm'),
    positiveText: t('products.delete'),
    negativeText: t('products.cancel'),
    onPositiveClick: async () => {
      try {
        await store.deleteProduct(id)
        message.success(t('products.deleteSuccess'))
      } catch {
        message.error(t('products.deleteFailed'))
      }
    },
  })
}

const currentPage = computed(() => Math.floor(store.offset / store.limit) + 1)
const pageCount = computed(() => Math.max(1, Math.ceil(store.total / store.limit)))

const categoryMap = computed(() => {
  const m: Record<string, string> = {}
  for (const c of store.categories) m[c.id] = c.name
  return m
})

const columns = computed(() => [
  {
    title: t('products.name'),
    key: 'name',
    ellipsis: true,
  },
  {
    title: t('products.sku'),
    key: 'sku',
    ellipsis: true,
    render(row: { sku: string | null }) {
      return row.sku ?? '—'
    },
  },
  {
    title: t('products.category'),
    key: 'category_id',
    ellipsis: true,
    render(row: { category_id: string | null }) {
      return row.category_id ? (categoryMap.value[row.category_id] ?? '—') : '—'
    },
  },
  {
    title: t('products.cost'),
    key: 'purchase_cost_excl_vat',
    render(row: { purchase_cost_excl_vat: string | null }) {
      return row.purchase_cost_excl_vat != null
        ? Number(row.purchase_cost_excl_vat).toFixed(2)
        : '—'
    },
  },
  {
    title: t('products.effectiveMargin'),
    key: 'effective_margin_rate',
    render(row: { effective_margin_rate: string | null }) {
      if (row.effective_margin_rate == null) return '—'
      return `${(Number(row.effective_margin_rate) * 100).toFixed(1)}%`
    },
  },
  {
    title: t('dict.active'),
    key: 'active',
    width: 80,
    render(row: { active: boolean }) {
      return h(NTag, { size: 'small', type: row.active ? 'success' : 'default' }, () =>
        row.active ? t('vat.active') : t('vat.inactive'),
      )
    },
  },
  {
    title: t('dict.actions'),
    key: 'actions',
    width: 96,
    align: 'center' as const,
    render(row: { id: string; name: string }) {
      return h(NSpace, { size: 4, justify: 'center', wrapItem: false }, () => [
        h(
          NButton,
          {
            size: 'small',
            quaternary: true,
            circle: true,
            title: t('products.edit'),
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
            title: t('products.delete'),
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
  <div class="product-list-page">
    <n-layout>
      <AppHeader />

      <n-layout-content class="app-content">
        <div class="product-list-container">
          <div class="product-list-header">
            <h2>{{ t('products.title') }}</h2>
            <n-space align="center" wrap>
              <n-input
                v-model:value="store.query"
                :placeholder="t('products.search')"
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
                :value="store.categoryFilter"
                :options="categoryOptions"
                style="width: 180px"
                :placeholder="t('products.allCategories')"
                clearable
                @update:value="handleCategoryChange"
              />
              <n-select
                :value="store.sortBy"
                :options="sortOptions"
                style="width: 160px"
                @update:value="handleSortChange"
              />
              <n-button type="primary" @click="handleCreate">
                <template #icon>
                  <n-icon><AddOutline /></n-icon>
                </template>
                {{ t('products.create') }}
              </n-button>
            </n-space>
          </div>

          <n-alert v-if="store.error" type="error" style="margin-bottom: 16px">
            {{ store.error }}
          </n-alert>

          <n-spin :show="store.loading">
            <template v-if="!store.loading && store.items.length === 0">
              <n-empty
                v-if="store.total === 0 && !store.query && !store.categoryFilter"
                :description="t('products.empty')"
              />
              <n-empty v-else :description="t('products.noResults')" />
            </template>

            <n-data-table
              v-else
              :columns="columns"
              :data="store.items"
              :bordered="false"
              :row-key="(row: { id: string }) => row.id"
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

.product-list-container {
  max-width: 1100px;
  margin: 0 auto;
  padding: 24px;
}

.product-list-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 16px;
  gap: 16px;
  flex-wrap: wrap;
}

.product-list-header h2 {
  margin: 0;
}

.pagination-container {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}
</style>
