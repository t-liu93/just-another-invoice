<script setup lang="ts">
/**
 * Dictionary settings panel content (M4 step 1).
 *
 * VAT Rates  – user-editable list (add / edit / delete).
 * VAT Treatments – read-only system list with side / effect / ICP annotation.
 */
import { onMounted, ref, computed, h } from 'vue'
import {
  NTabs,
  NTabPane,
  NDataTable,
  NButton,
  NSpace,
  NModal,
  NForm,
  NFormItem,
  NInput,
  NInputNumber,
  NSwitch,
  NAlert,
  NSpin,
  NTag,
  type DataTableColumns,
} from 'naive-ui'
import { useI18n } from 'vue-i18n'
import { useMessage, useDialog } from 'naive-ui'
import { AddOutline, CreateOutline, TrashOutline } from '@vicons/ionicons5'
import { NIcon } from 'naive-ui'
import { useVatStore } from '../../stores/vat'
import { ApiError } from '../../api/http'
import type { components } from '../../api/schema'

type VatRateRead = components['schemas']['VatRateRead']
type VatTreatmentRead = components['schemas']['VatTreatmentRead']
type VatTreatmentEffect = components['schemas']['VatTreatmentEffect']

const { t } = useI18n()
const message = useMessage()
const dialog = useDialog()
const store = useVatStore()

// --- Rate modal state ---
const showRateModal = ref(false)
const editingRateId = ref<string | null>(null)
const rateForm = ref({ label: '', percent: 0, active: true })
const savingRate = ref(false)

function openCreateRate() {
  editingRateId.value = null
  rateForm.value = { label: '', percent: 0, active: true }
  showRateModal.value = true
}

function openEditRate(row: VatRateRead) {
  editingRateId.value = row.id
  rateForm.value = { label: row.label, percent: Number(row.percent), active: row.active }
  showRateModal.value = true
}

async function saveRate() {
  if (!rateForm.value.label.trim()) {
    message.error(t('vat.rate.labelRequired'))
    return
  }
  savingRate.value = true
  try {
    const payload = {
      label: rateForm.value.label.trim(),
      percent: String(rateForm.value.percent),
      active: rateForm.value.active,
    }
    if (editingRateId.value) {
      await store.updateRate(editingRateId.value, payload)
    } else {
      await store.createRate(payload)
    }
    showRateModal.value = false
    message.success(t('vat.rate.saveSuccess'))
  } catch (e: unknown) {
    const msg = e instanceof ApiError ? e.message : t('vat.rate.saveFailed')
    message.error(msg)
  } finally {
    savingRate.value = false
  }
}

function confirmDeleteRate(row: VatRateRead) {
  dialog.warning({
    title: t('vat.rate.deleteConfirm'),
    content: row.label,
    positiveText: t('vat.rate.delete'),
    negativeText: t('vat.rate.cancel'),
    onPositiveClick: async () => {
      try {
        await store.deleteRate(row.id)
        message.success(t('vat.rate.deleteSuccess'))
      } catch {
        message.error(t('vat.rate.deleteFailed'))
      }
    },
  })
}

// --- VAT rate table columns ---
const rateColumns = computed<DataTableColumns<VatRateRead>>(() => [
  { title: t('vat.rate.label'), key: 'label' },
  {
    title: t('vat.rate.percent'),
    key: 'percent',
    render: (row) => `${Number(row.percent).toFixed(1)}%`,
  },
  {
    title: t('vat.rate.active'),
    key: 'active',
    render: (row) =>
      h(NTag, { type: row.active ? 'success' : 'default', size: 'small' }, {
        default: () => (row.active ? t('vat.active') : t('vat.inactive')),
      }),
  },
  {
    title: t('vat.rate.actions'),
    key: 'actions',
    render: (row) =>
      h(NSpace, { size: 'small' }, {
        default: () => [
          h(
            NButton,
            {
              size: 'small',
              quaternary: true,
              onClick: () => openEditRate(row),
            },
            { icon: () => h(NIcon, null, { default: () => h(CreateOutline) }) },
          ),
          h(
            NButton,
            {
              size: 'small',
              quaternary: true,
              type: 'error',
              onClick: () => confirmDeleteRate(row),
            },
            { icon: () => h(NIcon, null, { default: () => h(TrashOutline) }) },
          ),
        ],
      }),
  },
])

// --- Treatment table helpers ---
function effectLabel(effect: VatTreatmentEffect): string {
  switch (effect) {
    case 'APPLY_RATE': return t('vat.effect.applyRate')
    case 'ZERO_REVERSE': return t('vat.effect.zeroReverse')
    case 'ZERO_EXPORT': return t('vat.effect.zeroExport')
    case 'EXEMPT': return t('vat.effect.exempt')
  }
}

const treatmentColumns = computed<DataTableColumns<VatTreatmentRead>>(() => [
  { title: t('vat.treatment.code'), key: 'code', ellipsis: { tooltip: true } },
  { title: t('vat.treatment.label'), key: 'label' },
  {
    title: t('vat.treatment.side'),
    key: 'side',
    render: (row) =>
      h(NTag, { type: row.side === 'SALES' ? 'info' : 'warning', size: 'small' }, {
        default: () => (row.side === 'SALES' ? t('vat.side.sales') : t('vat.side.purchase')),
      }),
  },
  {
    title: t('vat.treatment.effect'),
    key: 'effect',
    render: (row) => effectLabel(row.effect),
  },
  {
    title: t('vat.treatment.requiresIcp'),
    key: 'requires_icp',
    render: (row) =>
      row.requires_icp
        ? h(NTag, { type: 'warning', size: 'small' }, { default: () => t('vat.yes') })
        : '—',
  },
  {
    title: t('vat.treatment.deductible'),
    key: 'deductible',
    render: (row) =>
      row.deductible === null || row.deductible === undefined
        ? '—'
        : h(NTag, { type: row.deductible ? 'success' : 'default', size: 'small' }, {
            default: () => (row.deductible ? t('vat.yes') : t('vat.no')),
          }),
  },
])

onMounted(async () => {
  await Promise.all([store.fetchRates(), store.fetchTreatments()])
})
</script>

<template>
  <div class="dict-settings">
    <n-tabs type="line" animated>
      <!-- VAT Rates -->
      <n-tab-pane name="rates" :tab="t('vat.rates')">
        <div class="tab-header">
          <n-button size="small" type="primary" @click="openCreateRate">
            <template #icon>
              <n-icon><AddOutline /></n-icon>
            </template>
            {{ t('vat.rate.create') }}
          </n-button>
        </div>

        <n-spin :show="store.loadingRates">
          <n-alert
            v-if="store.error"
            type="error"
            :title="t('vat.rate.loadFailed')"
            style="margin-bottom: 12px"
          />
          <n-data-table
            :columns="rateColumns"
            :data="store.rates"
            size="small"
            :bordered="false"
            :single-line="false"
          />
        </n-spin>
      </n-tab-pane>

      <!-- VAT Treatments (read-only) -->
      <n-tab-pane name="treatments" :tab="t('vat.treatments')">
        <n-spin :show="store.loadingTreatments">
          <n-alert
            v-if="store.error"
            type="error"
            :title="t('vat.treatment.loadFailed')"
            style="margin-bottom: 12px"
          />
          <n-data-table
            :columns="treatmentColumns"
            :data="store.treatments"
            size="small"
            :bordered="false"
            :single-line="false"
          />
        </n-spin>
      </n-tab-pane>
    </n-tabs>

    <!-- Rate add/edit modal -->
    <n-modal
      v-model:show="showRateModal"
      preset="card"
      :title="editingRateId ? t('vat.rate.edit') : t('vat.rate.create')"
      :style="{ width: '400px' }"
    >
      <n-form label-placement="left" label-width="100px">
        <n-form-item :label="t('vat.rate.label')">
          <n-input
            v-model:value="rateForm.label"
            :placeholder="t('vat.rate.labelPlaceholder')"
          />
        </n-form-item>
        <n-form-item :label="t('vat.rate.percent')">
          <n-input-number
            v-model:value="rateForm.percent"
            :min="0"
            :precision="3"
            :placeholder="t('vat.rate.percentPlaceholder')"
            style="width: 100%"
          />
        </n-form-item>
        <n-form-item :label="t('vat.rate.active')">
          <n-switch v-model:value="rateForm.active" />
        </n-form-item>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button @click="showRateModal = false">{{ t('vat.rate.cancel') }}</n-button>
          <n-button type="primary" :loading="savingRate" @click="saveRate">
            {{ t('vat.rate.save') }}
          </n-button>
        </n-space>
      </template>
    </n-modal>
  </div>
</template>

<style scoped>
.dict-settings {
  padding: 4px 0;
}

.tab-header {
  margin-bottom: 12px;
}
</style>
