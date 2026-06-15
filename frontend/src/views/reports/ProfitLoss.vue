<script setup lang="ts">
/**
 * P/L (Profit & Loss) report view – M10 step 1.
 *
 * Features:
 * - Date range picker (from / to)
 * - Granularity toggle (month / quarter)
 * - Summary KPI cards (revenue, expense, profit)
 * - Time-series table with all buckets
 */
import { computed, h, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  NAlert,
  NButton,
  NCard,
  NDataTable,
  NDatePicker,
  NGrid,
  NGridItem,
  NLayout,
  NLayoutContent,
  NRadioButton,
  NRadioGroup,
  NSpace,
  NSpin,
  NStatistic,
  NText,
} from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import AppHeader from '../../components/AppHeader.vue'
import { useReportsStore } from '../../stores/reports'
import type { ProfitLossSeriesItem } from '../../stores/reports'
import { localDateStr } from '../../utils/date'

const { t } = useI18n()
const store = useReportsStore()

// ─── Filters ─────────────────────────────────────────────────────────────────

const today = new Date()
const currentYear = today.getFullYear()

// Default: current year from Jan 1 to today
const dateRange = ref<[number, number]>([
  new Date(currentYear, 0, 1).getTime(),
  today.getTime(),
])

const granularity = ref<'month' | 'quarter'>('month')

// ─── Fetch ────────────────────────────────────────────────────────────────────

async function load() {
  if (!dateRange.value) return
  const [fromTs, toTs] = dateRange.value
  const from = localDateStr(new Date(fromTs))
  const to = localDateStr(new Date(toTs))
  await store.fetchProfitLoss(from, to, granularity.value)
}

onMounted(load)

// ─── Summary cards ───────────────────────────────────────────────────────────

function fmtMoney(v: string | number | null | undefined): string {
  if (v === null || v === undefined) return '—'
  const n = typeof v === 'string' ? parseFloat(v) : v
  return n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

const summaryRevenue = computed(() =>
  store.plReport ? fmtMoney(store.plReport.revenue_net) : '—',
)
const summaryExpense = computed(() =>
  store.plReport ? fmtMoney(store.plReport.expense_actual) : '—',
)
const summaryProfit = computed(() =>
  store.plReport ? fmtMoney(store.plReport.profit) : '—',
)

const profitPositive = computed(() => {
  if (!store.plReport) return true
  return parseFloat(store.plReport.profit) >= 0
})

// ─── Series table ─────────────────────────────────────────────────────────────

const tableColumns = computed<DataTableColumns<ProfitLossSeriesItem>>(() => [
  {
    title: t('reports.pl.period'),
    key: 'period',
    width: 120,
    render(row) {
      // Format period label by granularity: YYYY-MM or Q1 YYYY etc.
      if (granularity.value === 'quarter') {
        const d = new Date(row.period)
        const q = Math.floor(d.getMonth() / 3) + 1
        return `Q${q} ${d.getFullYear()}`
      }
      const d = new Date(row.period)
      return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
    },
  },
  {
    title: t('reports.pl.revenueNet'),
    key: 'revenue_net',
    align: 'right',
    render(row) {
      return fmtMoney(row.revenue_net)
    },
  },
  {
    title: t('reports.pl.expenseActual'),
    key: 'expense_actual',
    align: 'right',
    render(row) {
      return fmtMoney(row.expense_actual)
    },
  },
  {
    title: t('reports.pl.profit'),
    key: 'profit',
    align: 'right',
    render(row) {
      const val = parseFloat(row.profit)
      return h(NText, { type: val >= 0 ? 'success' : 'error' }, () => fmtMoney(row.profit))
    },
  },
])

</script>

<template>
  <div class="pl-report-page">
    <n-layout>
      <AppHeader />
      <n-layout-content class="app-content">
        <div class="report-container">
          <h2>{{ t('reports.pl.title') }}</h2>

          <!-- Filters -->
          <n-space align="center" :wrap="true" class="filters-row">
            <n-date-picker
              v-model:value="dateRange"
              type="daterange"
              :start-placeholder="t('reports.pl.from')"
              :end-placeholder="t('reports.pl.to')"
              clearable
              @update:value="load"
            />
            <n-radio-group v-model:value="granularity" @update:value="load">
              <n-radio-button value="month">{{ t('reports.pl.granMonth') }}</n-radio-button>
              <n-radio-button value="quarter">{{ t('reports.pl.granQuarter') }}</n-radio-button>
            </n-radio-group>
            <n-button :loading="store.plLoading" @click="load">
              {{ t('reports.pl.refresh') }}
            </n-button>
          </n-space>

          <!-- Error -->
          <n-alert v-if="store.plError" type="error" :title="t('common.error')" class="mt-4">
            {{ store.plError }}
          </n-alert>

          <!-- Loading -->
          <n-spin v-if="store.plLoading" :size="'large'" class="spinner" />

          <!-- Summary cards -->
          <n-grid
            v-if="store.plReport && !store.plLoading"
            :cols="3"
            :x-gap="16"
            :y-gap="16"
            class="summary-grid"
          >
            <n-grid-item>
              <n-card>
                <n-statistic :label="t('reports.pl.revenueNet')" :value="summaryRevenue" />
              </n-card>
            </n-grid-item>
            <n-grid-item>
              <n-card>
                <n-statistic :label="t('reports.pl.expenseActual')" :value="summaryExpense" />
              </n-card>
            </n-grid-item>
            <n-grid-item>
              <n-card>
                <n-statistic
                  :label="t('reports.pl.profit')"
                  :value="summaryProfit"
                  :value-style="{ color: profitPositive ? 'var(--n-color-success)' : 'var(--n-color-error)' }"
                />
              </n-card>
            </n-grid-item>
          </n-grid>

          <!-- Time-series table -->
          <n-card
            v-if="store.plReport && !store.plLoading"
            :title="t('reports.pl.seriesTitle')"
            class="series-card"
          >
            <n-data-table
              :columns="tableColumns"
              :data="store.plReport.series"
              size="small"
            />
          </n-card>
        </div>
      </n-layout-content>
    </n-layout>
  </div>
</template>

<style scoped>
.app-content {
  min-height: calc(100vh - 57px);
}

.report-container {
  max-width: 960px;
  margin: 0 auto;
  padding: 24px 16px;
}

.filters-row {
  margin-bottom: 16px;
}

.mt-4 {
  margin-top: 16px;
}

.spinner {
  display: flex;
  justify-content: center;
  margin: 40px 0;
}

.summary-grid {
  margin-bottom: 20px;
}

.series-card {
  margin-top: 8px;
}
</style>
