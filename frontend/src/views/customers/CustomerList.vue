<script setup lang="ts">
/**
 * Customer list page – search, pagination, delete confirmation.
 */
import { onMounted, onBeforeUnmount, computed, h } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useMessage, useDialog, NButton, NSpace, NInput, NDataTable, NAlert, NSpin, NPagination, NSelect } from 'naive-ui'
import { AddOutline, SearchOutline, CreateOutline, TrashOutline } from '@vicons/ionicons5'
import { NIcon } from 'naive-ui'
import { useCustomersStore } from '../../stores/customers'

const router = useRouter()
const { t } = useI18n()
const message = useMessage()
const dialog = useDialog()
const store = useCustomersStore()

onMounted(() => {
  store.fetchCustomers()
})

let searchTimer: ReturnType<typeof setTimeout> | null = null

/** Debounced live search: re-fetch ~300 ms after the user stops typing. */
function handleSearchInput() {
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(() => {
    store.offset = 0
    store.fetchCustomers()
  }, 300)
}

/** Immediate search (Enter key / clear): cancel any pending debounce first. */
function handleSearch() {
  if (searchTimer) clearTimeout(searchTimer)
  store.offset = 0
  store.fetchCustomers()
}

onBeforeUnmount(() => {
  if (searchTimer) clearTimeout(searchTimer)
})

function handlePageChange(page: number) {
  store.offset = (page - 1) * store.limit
  store.fetchCustomers()
}

function handleCreate() {
  router.push('/customers/new')
}

function handleEdit(id: string) {
  router.push(`/customers/${id}/edit`)
}

function handleDelete(_name: string, id: string) {
  dialog.warning({
    title: t('customers.delete'),
    content: t('customers.deleteConfirm'),
    positiveText: t('customers.delete'),
    negativeText: t('customers.cancel'),
    onPositiveClick: async () => {
      try {
        await store.deleteCustomer(id)
        message.success(t('customers.deleteSuccess'))
        store.fetchCustomers()
      } catch {
        message.error(t('customers.deleteFailed'))
      }
    },
  })
}

const currentPage = computed(() => Math.floor(store.offset / store.limit) + 1)
const pageCount = computed(() => Math.max(1, Math.ceil(store.total / store.limit)))

const sortOptions = computed(() => [
  { label: t('customers.sortByCreatedAt'), value: 'created_at' },
  { label: t('customers.sortByName'), value: 'name' },
])

function handleSortChange(val: 'name' | 'created_at') {
  store.sortBy = val
  store.offset = 0
  store.fetchCustomers()
}

const columns = computed(() => [
  {
    title: t('customers.name'),
    key: 'name',
    ellipsis: { tooltip: true },
  },
  {
    title: t('customers.companyName'),
    key: 'company_name',
    ellipsis: { tooltip: true },
  },
  {
    title: t('customers.email'),
    key: 'email',
    ellipsis: true,
  },
  {
    title: t('customers.phone'),
    key: 'phone',
    ellipsis: true,
  },
  {
    title: t('customers.currency'),
    key: 'currency',
    minWidth: 130,
  },
  {
    title: t('customers.actions'),
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
            title: t('customers.edit'),
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
            title: t('customers.delete'),
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
  <div class="customer-list-page">
    <div class="customer-list-container">
          <div class="customer-list-header">
            <h2>{{ t('customers.title') }}</h2>
            <n-space align="center">
              <n-input
                v-model:value="store.query"
                :placeholder="t('customers.search')"
                clearable
                style="width: 260px"
                @input="handleSearchInput"
                @keyup.enter="handleSearch"
                @clear="handleSearch"
              >
                <template #prefix>
                  <n-icon><SearchOutline /></n-icon>
                </template>
              </n-input>
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
                {{ t('customers.create') }}
              </n-button>
            </n-space>
          </div>

          <n-alert v-if="store.error" type="error" style="margin-bottom: 16px">
            {{ store.error }}
          </n-alert>

          <n-spin :show="store.loading">
            <template v-if="!store.loading && store.items.length === 0">
              <n-empty v-if="store.total === 0 && !store.query" :description="t('customers.empty')" />
              <n-empty v-else :description="t('customers.noResults')" />
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
</template>

<style scoped>
.customer-list-container {
  max-width: 960px;
  margin: 0 auto;
  padding: 24px;
}

.customer-list-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}

.customer-list-header h2 {
  margin: 0;
}

.pagination-container {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}
</style>
