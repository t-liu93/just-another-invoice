<script setup lang="ts">
/**
 * Dictionary settings panel content (M4 step 1 + step 2).
 *
 * Tab 1 – VAT Rates: user-editable list (add / edit / delete).
 * Tab 2 – VAT Treatments: read-only system list.
 * Tab 3 – Payment Methods: user-editable list.
 * Tab 4 – Expense Categories: user-editable list.
 * Tab 5 – Units: user-editable list.
 */
import { computed, h, onMounted, ref } from 'vue'
import {
  NAlert,
  NButton,
  NDataTable,
  NForm,
  NFormItem,
  NIcon,
  NInput,
  NInputNumber,
  NModal,
  NSpace,
  NSpin,
  NSwitch,
  NTabPane,
  NTabs,
  NTag,
  type DataTableColumns,
} from 'naive-ui'
import { useI18n } from 'vue-i18n'
import { useMessage, useDialog } from 'naive-ui'
import { AddOutline, CreateOutline, TrashOutline } from '@vicons/ionicons5'
import { useVatStore } from '../../stores/vat'
import {
  useExpenseCategoryStore,
  usePaymentMethodStore,
  useUnitStore,
} from '../../stores/dictionaries'
import { ApiError } from '../../api/http'
import type { components } from '../../api/schema'

type VatRateRead = components['schemas']['VatRateRead']
type VatTreatmentRead = components['schemas']['VatTreatmentRead']
type VatTreatmentEffect = components['schemas']['VatTreatmentEffect']
type PaymentMethodRead = components['schemas']['PaymentMethodRead']
type ExpenseCategoryRead = components['schemas']['ExpenseCategoryRead']
type UnitRead = components['schemas']['UnitRead']

const { t } = useI18n()
const message = useMessage()
const dialog = useDialog()

const vatStore = useVatStore()
const pmStore = usePaymentMethodStore()
const ecStore = useExpenseCategoryStore()
const unitStore = useUnitStore()

// ============================================================================
// VAT Rate modal
// ============================================================================

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
      await vatStore.updateRate(editingRateId.value, payload)
    } else {
      await vatStore.createRate(payload)
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
        await vatStore.deleteRate(row.id)
        message.success(t('vat.rate.deleteSuccess'))
      } catch {
        message.error(t('vat.rate.deleteFailed'))
      }
    },
  })
}

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
          h(NButton, { size: 'small', quaternary: true, onClick: () => openEditRate(row) }, {
            icon: () => h(NIcon, null, { default: () => h(CreateOutline) }),
          }),
          h(NButton, { size: 'small', quaternary: true, type: 'error', onClick: () => confirmDeleteRate(row) }, {
            icon: () => h(NIcon, null, { default: () => h(TrashOutline) }),
          }),
        ],
      }),
  },
])

// ============================================================================
// VAT Treatment columns (read-only)
// ============================================================================

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

// ============================================================================
// Generic simple-dictionary helpers (PaymentMethod / ExpenseCategory / Unit)
// ============================================================================

type SimpleItem = { id: string; name: string; active: boolean }

function makeSimpleActions<T extends SimpleItem>(
  onEdit: (row: T) => void,
  onDelete: (row: T) => void,
): DataTableColumns<T>[number] {
  return {
    title: t('dict.actions'),
    key: 'actions',
    render: (row: T) =>
      h(NSpace, { size: 'small' }, {
        default: () => [
          h(NButton, { size: 'small', quaternary: true, onClick: () => onEdit(row) }, {
            icon: () => h(NIcon, null, { default: () => h(CreateOutline) }),
          }),
          h(NButton, { size: 'small', quaternary: true, type: 'error', onClick: () => onDelete(row) }, {
            icon: () => h(NIcon, null, { default: () => h(TrashOutline) }),
          }),
        ],
      }),
  }
}

function makeActiveTag<T extends { active: boolean }>(row: T) {
  return h(NTag, { type: row.active ? 'success' : 'default', size: 'small' }, {
    default: () => (row.active ? t('vat.active') : t('vat.inactive')),
  })
}

// ============================================================================
// Payment Methods
// ============================================================================

const showPmModal = ref(false)
const editingPmId = ref<string | null>(null)
const pmForm = ref({ name: '', active: true })
const savingPm = ref(false)

function openCreatePm() {
  editingPmId.value = null
  pmForm.value = { name: '', active: true }
  showPmModal.value = true
}

function openEditPm(row: PaymentMethodRead) {
  editingPmId.value = row.id
  pmForm.value = { name: row.name, active: row.active }
  showPmModal.value = true
}

async function savePm() {
  if (!pmForm.value.name.trim()) {
    message.error(t('paymentMethods.nameRequired'))
    return
  }
  savingPm.value = true
  try {
    const payload = { name: pmForm.value.name.trim(), active: pmForm.value.active }
    if (editingPmId.value) {
      await pmStore.update(editingPmId.value, payload)
    } else {
      await pmStore.create(payload)
    }
    showPmModal.value = false
    message.success(t('paymentMethods.saveSuccess'))
  } catch (e: unknown) {
    message.error(e instanceof ApiError ? e.message : t('paymentMethods.saveFailed'))
  } finally {
    savingPm.value = false
  }
}

function confirmDeletePm(row: PaymentMethodRead) {
  dialog.warning({
    title: t('paymentMethods.deleteConfirm'),
    content: row.name,
    positiveText: t('paymentMethods.delete'),
    negativeText: t('paymentMethods.cancel'),
    onPositiveClick: async () => {
      try {
        await pmStore.remove(row.id)
        message.success(t('paymentMethods.deleteSuccess'))
      } catch {
        message.error(t('paymentMethods.deleteFailed'))
      }
    },
  })
}

const pmColumns = computed<DataTableColumns<PaymentMethodRead>>(() => [
  { title: t('paymentMethods.name'), key: 'name' },
  { title: t('dict.active'), key: 'active', render: makeActiveTag },
  makeSimpleActions(openEditPm, confirmDeletePm),
])

// ============================================================================
// Expense Categories
// ============================================================================

const showEcModal = ref(false)
const editingEcId = ref<string | null>(null)
const ecForm = ref<{
  name: string
  description: string
  default_deductible: boolean | null
  active: boolean
}>({ name: '', description: '', default_deductible: null, active: true })
const savingEc = ref(false)

function openCreateEc() {
  editingEcId.value = null
  ecForm.value = { name: '', description: '', default_deductible: null, active: true }
  showEcModal.value = true
}

function openEditEc(row: ExpenseCategoryRead) {
  editingEcId.value = row.id
  ecForm.value = {
    name: row.name,
    description: row.description ?? '',
    default_deductible: row.default_deductible ?? null,
    active: row.active,
  }
  showEcModal.value = true
}

async function saveEc() {
  if (!ecForm.value.name.trim()) {
    message.error(t('expenseCategories.nameRequired'))
    return
  }
  savingEc.value = true
  try {
    const payload = {
      name: ecForm.value.name.trim(),
      description: ecForm.value.description || null,
      default_deductible: ecForm.value.default_deductible,
      active: ecForm.value.active,
    }
    if (editingEcId.value) {
      await ecStore.update(editingEcId.value, payload)
    } else {
      await ecStore.create(payload)
    }
    showEcModal.value = false
    message.success(t('expenseCategories.saveSuccess'))
  } catch (e: unknown) {
    message.error(e instanceof ApiError ? e.message : t('expenseCategories.saveFailed'))
  } finally {
    savingEc.value = false
  }
}

function confirmDeleteEc(row: ExpenseCategoryRead) {
  dialog.warning({
    title: t('expenseCategories.deleteConfirm'),
    content: row.name,
    positiveText: t('expenseCategories.delete'),
    negativeText: t('expenseCategories.cancel'),
    onPositiveClick: async () => {
      try {
        await ecStore.remove(row.id)
        message.success(t('expenseCategories.deleteSuccess'))
      } catch {
        message.error(t('expenseCategories.deleteFailed'))
      }
    },
  })
}

function deductibleTag(row: ExpenseCategoryRead) {
  if (row.default_deductible === null || row.default_deductible === undefined) return '—'
  return h(NTag, { type: row.default_deductible ? 'success' : 'default', size: 'small' }, {
    default: () => (row.default_deductible ? t('vat.yes') : t('vat.no')),
  })
}

const ecColumns = computed<DataTableColumns<ExpenseCategoryRead>>(() => [
  { title: t('expenseCategories.name'), key: 'name' },
  {
    title: t('expenseCategories.defaultDeductible'),
    key: 'default_deductible',
    render: deductibleTag,
  },
  { title: t('dict.active'), key: 'active', render: makeActiveTag },
  makeSimpleActions(openEditEc, confirmDeleteEc),
])

// ============================================================================
// Units
// ============================================================================

const showUnitModal = ref(false)
const editingUnitId = ref<string | null>(null)
const unitForm = ref({ code: '', name: '', active: true })
const savingUnit = ref(false)

function openCreateUnit() {
  editingUnitId.value = null
  unitForm.value = { code: '', name: '', active: true }
  showUnitModal.value = true
}

function openEditUnit(row: UnitRead) {
  editingUnitId.value = row.id
  unitForm.value = { code: row.code, name: row.name, active: row.active }
  showUnitModal.value = true
}

async function saveUnit() {
  if (!unitForm.value.code.trim() || !unitForm.value.name.trim()) {
    message.error(t('units.codeAndNameRequired'))
    return
  }
  savingUnit.value = true
  try {
    const payload = {
      code: unitForm.value.code.trim(),
      name: unitForm.value.name.trim(),
      active: unitForm.value.active,
    }
    if (editingUnitId.value) {
      await unitStore.update(editingUnitId.value, payload)
    } else {
      await unitStore.create(payload)
    }
    showUnitModal.value = false
    message.success(t('units.saveSuccess'))
  } catch (e: unknown) {
    message.error(e instanceof ApiError ? e.message : t('units.saveFailed'))
  } finally {
    savingUnit.value = false
  }
}

function confirmDeleteUnit(row: UnitRead) {
  dialog.warning({
    title: t('units.deleteConfirm'),
    content: `${row.code} – ${row.name}`,
    positiveText: t('units.delete'),
    negativeText: t('units.cancel'),
    onPositiveClick: async () => {
      try {
        await unitStore.remove(row.id)
        message.success(t('units.deleteSuccess'))
      } catch {
        message.error(t('units.deleteFailed'))
      }
    },
  })
}

const unitColumns = computed<DataTableColumns<UnitRead>>(() => [
  { title: t('units.code'), key: 'code' },
  { title: t('units.name'), key: 'name' },
  { title: t('dict.active'), key: 'active', render: makeActiveTag },
  makeSimpleActions(openEditUnit, confirmDeleteUnit),
])

// ============================================================================
// Mount – load all dictionaries
// ============================================================================

onMounted(async () => {
  await Promise.all([
    vatStore.fetchRates(),
    vatStore.fetchTreatments(),
    pmStore.fetchAll(),
    ecStore.fetchAll(),
    unitStore.fetchAll(),
  ])
})
</script>

<template>
  <div class="dict-settings">
    <n-tabs type="line" animated>
      <!-- VAT Rates -->
      <n-tab-pane name="rates" :tab="t('vat.rates')">
        <div class="tab-header">
          <n-button size="small" type="primary" @click="openCreateRate">
            <template #icon><n-icon><AddOutline /></n-icon></template>
            {{ t('vat.rate.create') }}
          </n-button>
        </div>
        <n-spin :show="vatStore.loadingRates">
          <n-alert v-if="vatStore.error" type="error" :title="t('vat.rate.loadFailed')" style="margin-bottom:12px" />
          <n-data-table :columns="rateColumns" :data="vatStore.rates" size="small" :bordered="false" :single-line="false" />
        </n-spin>
      </n-tab-pane>

      <!-- VAT Treatments (read-only) -->
      <n-tab-pane name="treatments" :tab="t('vat.treatments')">
        <n-spin :show="vatStore.loadingTreatments">
          <n-alert v-if="vatStore.error" type="error" :title="t('vat.treatment.loadFailed')" style="margin-bottom:12px" />
          <n-data-table :columns="treatmentColumns" :data="vatStore.treatments" size="small" :bordered="false" :single-line="false" />
        </n-spin>
      </n-tab-pane>

      <!-- Payment Methods -->
      <n-tab-pane name="paymentMethods" :tab="t('paymentMethods.title')">
        <div class="tab-header">
          <n-button size="small" type="primary" @click="openCreatePm">
            <template #icon><n-icon><AddOutline /></n-icon></template>
            {{ t('paymentMethods.create') }}
          </n-button>
        </div>
        <n-spin :show="pmStore.loading">
          <n-alert v-if="pmStore.error" type="error" :title="t('paymentMethods.loadFailed')" style="margin-bottom:12px" />
          <n-data-table :columns="pmColumns" :data="pmStore.items" size="small" :bordered="false" :single-line="false" />
        </n-spin>
      </n-tab-pane>

      <!-- Expense Categories -->
      <n-tab-pane name="expenseCategories" :tab="t('expenseCategories.title')">
        <div class="tab-header">
          <n-button size="small" type="primary" @click="openCreateEc">
            <template #icon><n-icon><AddOutline /></n-icon></template>
            {{ t('expenseCategories.create') }}
          </n-button>
        </div>
        <n-spin :show="ecStore.loading">
          <n-alert v-if="ecStore.error" type="error" :title="t('expenseCategories.loadFailed')" style="margin-bottom:12px" />
          <n-data-table :columns="ecColumns" :data="ecStore.items" size="small" :bordered="false" :single-line="false" />
        </n-spin>
      </n-tab-pane>

      <!-- Units -->
      <n-tab-pane name="units" :tab="t('units.title')">
        <div class="tab-header">
          <n-button size="small" type="primary" @click="openCreateUnit">
            <template #icon><n-icon><AddOutline /></n-icon></template>
            {{ t('units.create') }}
          </n-button>
        </div>
        <n-spin :show="unitStore.loading">
          <n-alert v-if="unitStore.error" type="error" :title="t('units.loadFailed')" style="margin-bottom:12px" />
          <n-data-table :columns="unitColumns" :data="unitStore.items" size="small" :bordered="false" :single-line="false" />
        </n-spin>
      </n-tab-pane>
    </n-tabs>

    <!-- VAT Rate modal -->
    <n-modal v-model:show="showRateModal" preset="card" :title="editingRateId ? t('vat.rate.edit') : t('vat.rate.create')" :style="{ width: '400px' }">
      <n-form label-placement="left" label-width="100px">
        <n-form-item :label="t('vat.rate.label')">
          <n-input v-model:value="rateForm.label" :placeholder="t('vat.rate.labelPlaceholder')" />
        </n-form-item>
        <n-form-item :label="t('vat.rate.percent')">
          <n-input-number v-model:value="rateForm.percent" :min="0" :precision="3" :placeholder="t('vat.rate.percentPlaceholder')" style="width:100%" />
        </n-form-item>
        <n-form-item :label="t('vat.rate.active')">
          <n-switch v-model:value="rateForm.active" />
        </n-form-item>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button @click="showRateModal = false">{{ t('vat.rate.cancel') }}</n-button>
          <n-button type="primary" :loading="savingRate" @click="saveRate">{{ t('vat.rate.save') }}</n-button>
        </n-space>
      </template>
    </n-modal>

    <!-- Payment Method modal -->
    <n-modal v-model:show="showPmModal" preset="card" :title="editingPmId ? t('paymentMethods.edit') : t('paymentMethods.create')" :style="{ width: '400px' }">
      <n-form label-placement="left" label-width="100px">
        <n-form-item :label="t('paymentMethods.name')">
          <n-input v-model:value="pmForm.name" :placeholder="t('paymentMethods.namePlaceholder')" />
        </n-form-item>
        <n-form-item :label="t('dict.active')">
          <n-switch v-model:value="pmForm.active" />
        </n-form-item>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button @click="showPmModal = false">{{ t('paymentMethods.cancel') }}</n-button>
          <n-button type="primary" :loading="savingPm" @click="savePm">{{ t('paymentMethods.save') }}</n-button>
        </n-space>
      </template>
    </n-modal>

    <!-- Expense Category modal -->
    <n-modal v-model:show="showEcModal" preset="card" :title="editingEcId ? t('expenseCategories.edit') : t('expenseCategories.create')" :style="{ width: '440px' }">
      <n-form label-placement="left" label-width="120px">
        <n-form-item :label="t('expenseCategories.name')">
          <n-input v-model:value="ecForm.name" :placeholder="t('expenseCategories.namePlaceholder')" />
        </n-form-item>
        <n-form-item :label="t('expenseCategories.description')">
          <n-input v-model:value="ecForm.description" type="textarea" :rows="2" :placeholder="t('expenseCategories.descriptionPlaceholder')" />
        </n-form-item>
        <n-form-item :label="t('expenseCategories.defaultDeductible')">
          <n-space>
            <n-button
              size="small"
              :type="ecForm.default_deductible === true ? 'primary' : 'default'"
              @click="ecForm.default_deductible = true"
            >{{ t('vat.yes') }}</n-button>
            <n-button
              size="small"
              :type="ecForm.default_deductible === false ? 'primary' : 'default'"
              @click="ecForm.default_deductible = false"
            >{{ t('vat.no') }}</n-button>
            <n-button
              size="small"
              :type="ecForm.default_deductible === null ? 'primary' : 'default'"
              @click="ecForm.default_deductible = null"
            >—</n-button>
          </n-space>
        </n-form-item>
        <n-form-item :label="t('dict.active')">
          <n-switch v-model:value="ecForm.active" />
        </n-form-item>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button @click="showEcModal = false">{{ t('expenseCategories.cancel') }}</n-button>
          <n-button type="primary" :loading="savingEc" @click="saveEc">{{ t('expenseCategories.save') }}</n-button>
        </n-space>
      </template>
    </n-modal>

    <!-- Unit modal -->
    <n-modal v-model:show="showUnitModal" preset="card" :title="editingUnitId ? t('units.edit') : t('units.create')" :style="{ width: '400px' }">
      <n-form label-placement="left" label-width="100px">
        <n-form-item :label="t('units.code')">
          <n-input v-model:value="unitForm.code" :placeholder="t('units.codePlaceholder')" />
        </n-form-item>
        <n-form-item :label="t('units.name')">
          <n-input v-model:value="unitForm.name" :placeholder="t('units.namePlaceholder')" />
        </n-form-item>
        <n-form-item :label="t('dict.active')">
          <n-switch v-model:value="unitForm.active" />
        </n-form-item>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button @click="showUnitModal = false">{{ t('units.cancel') }}</n-button>
          <n-button type="primary" :loading="savingUnit" @click="saveUnit">{{ t('units.save') }}</n-button>
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
