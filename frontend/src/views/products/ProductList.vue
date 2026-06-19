<script setup lang="ts">
/**
 * Product catalogue list page – search, category filter, pagination, sort, Excel import.
 * Internal cost/margin fields shown only to the owner (no customer-facing outputs).
 */
import { onMounted, onBeforeUnmount, computed, h, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import {
  useMessage, useDialog,
  NButton, NSpace, NInput, NDataTable, NAlert, NSpin,
  NPagination, NSelect, NTag, NModal, NScrollbar,
  type SelectOption,
} from 'naive-ui'
import { AddOutline, SearchOutline, CreateOutline, TrashOutline, CloudUploadOutline } from '@vicons/ionicons5'
import { NIcon } from 'naive-ui'
import { useProductsStore } from '../../stores/products'
import type { components } from '../../api/schema'

type ProductImportRow = components['schemas']['ProductImportRow']
type ProductImportRowError = components['schemas']['ProductImportRowError']

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

// ---------------------------------------------------------------------------
// Excel import
// ---------------------------------------------------------------------------

const KNOWN_COLUMNS: Record<string, keyof ProductImportRow> = {
  name: 'name',
  sku: 'sku',
  category: 'category_name',
  category_name: 'category_name',
  unit: 'unit_code',
  unit_code: 'unit_code',
  cost: 'purchase_cost_excl_vat',
  purchase_cost_excl_vat: 'purchase_cost_excl_vat',
  margin: 'margin_rate',
  margin_rate: 'margin_rate',
  vat: 'vat_percent',
  vat_percent: 'vat_percent',
}

const showImportModal = ref(false)
const importStep = ref<'paste' | 'preview' | 'result'>('paste')
const importTsv = ref('')
const importParseError = ref<string | null>(null)
const importHeaders = ref<string[]>([])
const importRows = ref<ProductImportRow[]>([])
const importSubmitting = ref(false)
const importCreated = ref(0)
const importUpdated = ref(0)
const importErrors = ref<ProductImportRowError[]>([])

function openImportModal() {
  importStep.value = 'paste'
  importTsv.value = ''
  importParseError.value = null
  importHeaders.value = []
  importRows.value = []
  importSubmitting.value = false
  importCreated.value = 0
  importUpdated.value = 0
  importErrors.value = []
  showImportModal.value = true
}

function parseTsv() {
  importParseError.value = null
  importRows.value = []
  importHeaders.value = []

  const raw = importTsv.value.trim()
  if (!raw) {
    importParseError.value = t('products.import.parseError')
    return
  }

  const lines = raw.split(/\r?\n/)
  if (lines.length < 2) {
    importParseError.value = t('products.import.parseError')
    return
  }

  const headerLine = lines[0].split('\t').map((h) => h.trim().toLowerCase())
  const mappedHeaders: (keyof ProductImportRow | null)[] = headerLine.map(
    (h) => KNOWN_COLUMNS[h] ?? null,
  )

  if (!mappedHeaders.includes('name')) {
    importParseError.value = t('products.import.parseError')
    return
  }

  importHeaders.value = headerLine

  const rows: ProductImportRow[] = []
  for (let i = 1; i < lines.length; i++) {
    const line = lines[i]
    if (!line.trim()) continue
    const cells = line.split('\t').map((c) => c.trim())
    const row: Record<string, string | null> = {}
    for (let j = 0; j < mappedHeaders.length; j++) {
      const field = mappedHeaders[j]
      if (field) {
        row[field] = cells[j]?.trim() || null
      }
    }
    const importRow: ProductImportRow = {
      name: row['name'] ?? '',
      sku: row['sku'] ?? null,
      category_name: row['category_name'] ?? null,
      unit_code: row['unit_code'] ?? null,
      purchase_cost_excl_vat: row['purchase_cost_excl_vat'] ?? null,
      margin_rate: row['margin_rate'] ?? null,
      vat_percent: row['vat_percent'] ?? null,
    }
    rows.push(importRow)
  }

  if (rows.length === 0) {
    importParseError.value = t('products.import.parseError')
    return
  }

  importRows.value = rows
  importStep.value = 'preview'
}

async function submitImport() {
  if (importSubmitting.value) return // guard against double submit
  importSubmitting.value = true
  try {
    const result = await store.importProducts({ rows: importRows.value })
    importCreated.value = result.created
    importUpdated.value = result.updated ?? 0
    importErrors.value = result.errors
    importStep.value = 'result'
    if (result.created > 0 || (result.updated ?? 0) > 0) {
      await store.fetchProducts()
    }
  } catch {
    message.error(t('products.import.importFailed'))
  } finally {
    importSubmitting.value = false
  }
}

function closeImportModal() {
  showImportModal.value = false
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
    ellipsis: { tooltip: true },
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
    title: t('products.categoryLabel'),
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
    width: 140,
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
              <n-button @click="openImportModal">
                <template #icon>
                  <n-icon><CloudUploadOutline /></n-icon>
                </template>
                {{ t('products.import.button') }}
              </n-button>
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
  </div>

  <!-- Excel Import Modal -->
  <n-modal
    v-model:show="showImportModal"
    :mask-closable="importStep !== 'result'"
    preset="card"
    style="width: 680px; max-width: 96vw"
    :title="t('products.import.title')"
    :bordered="false"
  >
    <!-- Step: paste TSV -->
    <div v-if="importStep === 'paste'">
      <p style="margin: 0 0 8px; font-size: 13px; color: var(--n-text-color-3)">
        {{ t('products.import.pasteHint') }}
      </p>
      <p style="margin: 0 0 4px; font-size: 12px; color: var(--n-text-color-3)">
        {{ t('products.import.columnHelp') }}
      </p>
      <p style="margin: 0 0 12px; font-size: 12px; color: var(--n-text-color-3)">
        {{ t('products.import.marginHelp') }}
      </p>
      <textarea
        v-model="importTsv"
        style="
          width: 100%;
          min-height: 180px;
          font-family: monospace;
          font-size: 12px;
          padding: 8px;
          box-sizing: border-box;
          border: 1px solid var(--n-border-color);
          border-radius: 3px;
          background: var(--n-color);
          color: var(--n-text-color);
          resize: vertical;
        "
        placeholder="name&#9;sku&#9;category_name&#9;unit_code&#10;330W Panel&#9;SP330&#9;Solar&#9;pcs"
      />
      <n-alert v-if="importParseError" type="error" style="margin-top: 8px">
        {{ importParseError }}
      </n-alert>
    </div>

    <!-- Step: preview -->
    <div v-else-if="importStep === 'preview'">
      <p style="margin: 0 0 12px; font-size: 13px">
        {{ t('products.import.previewTitle', { count: importRows.length }) }}
      </p>
      <n-scrollbar style="max-height: 320px">
        <table style="width: 100%; border-collapse: collapse; font-size: 12px">
          <thead>
            <tr>
              <th
                v-for="col in importHeaders"
                :key="col"
                style="
                  text-align: left;
                  padding: 4px 8px;
                  border-bottom: 1px solid var(--n-border-color);
                  white-space: nowrap;
                  font-weight: 600;
                "
              >
                {{ col }}
              </th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(row, idx) in importRows" :key="idx">
              <td
                v-for="col in importHeaders"
                :key="col"
                style="padding: 3px 8px; border-bottom: 1px solid var(--n-border-color)"
              >
                {{ (row as Record<string, unknown>)[KNOWN_COLUMNS[col] ?? col] ?? '' }}
              </td>
            </tr>
          </tbody>
        </table>
      </n-scrollbar>
    </div>

    <!-- Step: result -->
    <div v-else-if="importStep === 'result'">
      <n-alert v-if="importCreated > 0" type="success" style="margin-bottom: 12px">
        {{ t('products.import.resultCreated', { n: importCreated }) }}
      </n-alert>
      <n-alert v-if="importUpdated > 0" type="info" style="margin-bottom: 12px">
        {{ t('products.import.resultUpdated', { n: importUpdated }) }}
      </n-alert>
      <n-alert
        v-if="importCreated === 0 && importUpdated === 0 && importErrors.length === 0"
        type="warning"
        style="margin-bottom: 12px"
      >
        {{ t('products.import.noData') }}
      </n-alert>
      <div v-if="importErrors.length > 0">
        <p style="margin: 0 0 6px; font-weight: 600">
          {{ t('products.import.resultErrors', { n: importErrors.length }) }}
        </p>
        <n-scrollbar style="max-height: 200px">
          <div
            v-for="err in importErrors"
            :key="err.row"
            style="font-size: 13px; margin-bottom: 4px"
          >
            <strong>{{ t('products.import.resultErrorRow', { row: err.row + 1 }) }}:</strong>
            {{ err.message }}
          </div>
        </n-scrollbar>
      </div>
    </div>

    <!-- Unified action buttons (NCard's #action slot is the correct slot for interactive buttons) -->
    <template #action>
      <n-space justify="end">
        <template v-if="importStep === 'paste'">
          <n-button @click="closeImportModal">{{ t('products.import.cancel') }}</n-button>
          <n-button type="primary" @click="parseTsv">
            {{ t('products.import.previewButton') }}
          </n-button>
        </template>
        <template v-else-if="importStep === 'preview'">
          <n-button @click="importStep = 'paste'">{{ t('products.import.cancel') }}</n-button>
          <!--
            IMPORTANT: do NOT put a reactive prop (:loading / :disabled) on the
            clickable Import button. In the production (optimized) Vue build that
            generates a PROPS patch flag on the component vnode which drops the
            @click dispatch entirely — the button looks enabled but clicks do
            nothing (works in dev, silently broken in `vite build`). Instead we
            swap to a separate static-`loading` button while submitting.
          -->
          <n-button v-if="importSubmitting" type="primary" loading disabled>
            {{ t('products.import.importButton') }}
          </n-button>
          <n-button v-else type="primary" @click="submitImport">
            {{ t('products.import.importButton') }}
          </n-button>
        </template>
        <template v-else>
          <n-button type="primary" @click="closeImportModal">
            {{ t('products.import.close') }}
          </n-button>
        </template>
      </n-space>
    </template>
  </n-modal>
</template>

<style scoped>
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
