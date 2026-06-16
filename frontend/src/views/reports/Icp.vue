<script setup lang="ts">
/**
 * ICP (Opgaaf ICP) report view – M10 step 3.
 *
 * Features:
 * - Year + quarter selector
 * - Customer-grouped table (customer_name / country_code / vat_id / net_amount)
 * - Missing vat_id / country_code rows highlighted with warning tag
 * - total_net displayed with note that it should match BTW box 3b
 * - Warnings list (advisory, required for Opgaaf ICP filing)
 * - Disclaimer (D-DISCLAIMER)
 */
import { computed, h, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  NAlert,
  NButton,
  NCard,
  NDataTable,
  NGrid,
  NGridItem,
  NSelect,
  NSpin,
  NStatistic,
  NTag,
  NText,
} from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import { useReportsStore } from '../../stores/reports'
import type { IcpLine } from '../../stores/reports'

const { t } = useI18n()
const store = useReportsStore()

// ─── Selectors ───────────────────────────────────────────────────────────────

const currentYear = new Date().getFullYear()
const selectedYear = ref<number>(currentYear)
const selectedQuarter = ref<number>(Math.ceil((new Date().getMonth() + 1) / 3))

const yearOptions = computed(() => {
  const years = []
  for (let y = currentYear; y >= currentYear - 5; y--) {
    years.push({ label: String(y), value: y })
  }
  return years
})

const quarterOptions = computed(() => [
  { label: t('reports.icp.q1'), value: 1 },
  { label: t('reports.icp.q2'), value: 2 },
  { label: t('reports.icp.q3'), value: 3 },
  { label: t('reports.icp.q4'), value: 4 },
])

// ─── Fetch ────────────────────────────────────────────────────────────────────

async function load() {
  await store.fetchIcp(selectedYear.value, selectedQuarter.value)
}

onMounted(load)

// ─── Data helpers ─────────────────────────────────────────────────────────────

function fmtMoney(v: string | number | null | undefined): string {
  if (v === null || v === undefined) return '—'
  const n = typeof v === 'string' ? parseFloat(v) : v
  return n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

// ─── Table columns ────────────────────────────────────────────────────────────

const columns = computed<DataTableColumns<IcpLine>>(() => [
  {
    title: t('reports.icp.colCustomer'),
    key: 'customer_name',
    minWidth: 180,
  },
  {
    title: t('reports.icp.colCountry'),
    key: 'country_code',
    width: 120,
    render: (row: IcpLine) => {
      if (row.country_code) return row.country_code
      return h(
        NTag,
        { type: 'warning', size: 'small', style: 'font-size: 11px' },
        { default: () => t('reports.icp.missingCountry') },
      )
    },
  },
  {
    title: t('reports.icp.colVatId'),
    key: 'vat_id',
    minWidth: 150,
    render: (row: IcpLine) => {
      if (row.vat_id) return row.vat_id
      return h(
        NTag,
        { type: 'warning', size: 'small', style: 'font-size: 11px' },
        { default: () => t('reports.icp.missingVatId') },
      )
    },
  },
  {
    title: t('reports.icp.colNetAmount'),
    key: 'net_amount',
    align: 'right',
    render: (row: IcpLine) => fmtMoney(row.net_amount),
  },
])

// ─── Totals ───────────────────────────────────────────────────────────────────

const totalNet = computed<string>(() => {
  const r = store.icpReport
  return r ? fmtMoney(r.total_net) : '—'
})

// ─── Period display ───────────────────────────────────────────────────────────

const periodLabel = computed<string>(() => {
  return `${selectedYear.value} Q${selectedQuarter.value}`
})

// ─── Row class for highlighting missing fields ────────────────────────────────

function rowClass(row: IcpLine): string {
  if (!row.vat_id || !row.country_code) return 'icp-row-warning'
  return ''
}
</script>

<template>
  <div class="icp-page page-content">
      <!-- Page header -->
      <div class="page-header">
        <h1 class="page-title">{{ t('reports.icp.title') }}</h1>
      </div>

      <!-- Selectors -->
      <n-card style="margin-bottom: 16px">
        <n-grid :cols="4" :x-gap="12" :y-gap="12">
          <n-grid-item>
            <div class="field-label">{{ t('reports.icp.year') }}</div>
            <n-select
              v-model:value="selectedYear"
              :options="yearOptions"
              style="width: 120px"
            />
          </n-grid-item>
          <n-grid-item>
            <div class="field-label">{{ t('reports.icp.quarter') }}</div>
            <n-select
              v-model:value="selectedQuarter"
              :options="quarterOptions"
              style="width: 180px"
            />
          </n-grid-item>
          <n-grid-item :span="2" style="display: flex; align-items: flex-end">
            <n-button type="primary" :loading="store.icpLoading" @click="load">
              {{ t('reports.icp.refresh') }}
            </n-button>
          </n-grid-item>
        </n-grid>
      </n-card>

      <!-- Loading / Error -->
      <div v-if="store.icpLoading" style="text-align: center; padding: 48px">
        <n-spin size="large" />
      </div>

      <n-alert
        v-else-if="store.icpError"
        type="error"
        :show-icon="true"
        style="margin-bottom: 16px"
      >
        {{ store.icpError }}
      </n-alert>

      <template v-else-if="store.icpReport">
        <!-- Period label -->
        <div style="margin-bottom: 8px; color: #666; font-size: 13px">
          {{ t('reports.icp.period') }}: <strong>{{ periodLabel }}</strong>
        </div>

        <!-- Warnings -->
        <n-alert
          v-if="(store.icpReport.warnings ?? []).length > 0"
          type="warning"
          :show-icon="true"
          style="margin-bottom: 16px"
        >
          <div style="font-weight: 600; margin-bottom: 4px">{{ t('reports.icp.warnings') }}</div>
          <ul style="margin: 0; padding-left: 20px">
            <li v-for="(w, i) in store.icpReport.warnings" :key="i">{{ w }}</li>
          </ul>
        </n-alert>

        <!-- Empty state -->
        <n-alert
          v-if="store.icpReport.lines.length === 0"
          type="info"
          :show-icon="true"
          style="margin-bottom: 16px"
        >
          {{ t('reports.icp.empty') }}
        </n-alert>

        <!-- Lines table -->
        <n-card v-else :title="t('reports.icp.linesTitle')" style="margin-bottom: 16px">
          <n-data-table
            :columns="columns"
            :data="store.icpReport.lines"
            :row-key="(row: IcpLine) => row.customer_id"
            :row-class-name="rowClass"
            :pagination="false"
            size="small"
          />
        </n-card>

        <!-- Total net -->
        <n-card style="margin-bottom: 16px">
          <n-grid :cols="2" :x-gap="24" :y-gap="16">
            <n-grid-item>
              <n-statistic :label="t('reports.icp.totalNet')">
                <n-text style="font-weight: 700; font-size: 20px">{{ totalNet }}</n-text>
              </n-statistic>
              <div style="margin-top: 6px; font-size: 12px; color: #999">
                {{ t('reports.icp.totalNetHelp') }}
              </div>
            </n-grid-item>
          </n-grid>
        </n-card>

        <!-- Disclaimer (D-DISCLAIMER) -->
        <n-alert type="default" :show-icon="false" style="margin-bottom: 16px">
          <div style="font-weight: 600; margin-bottom: 4px">{{ t('reports.icp.disclaimer') }}</div>
          <n-text depth="3" style="font-size: 12px">
            {{ t('reports.icp.disclaimerText') }}
          </n-text>
        </n-alert>
      </template>
  </div>
</template>

<style scoped>
.page-content {
  padding: 24px;
  max-width: 960px;
  margin: 0 auto;
}

.page-header {
  margin-bottom: 20px;
}

.page-title {
  font-size: 24px;
  font-weight: 600;
  margin: 0;
}

.field-label {
  font-size: 12px;
  color: #666;
  margin-bottom: 4px;
}
</style>

<style>
/* Global: highlight rows with missing ICP fields */
.icp-row-warning td {
  background-color: #fffbe6 !important;
}
</style>
