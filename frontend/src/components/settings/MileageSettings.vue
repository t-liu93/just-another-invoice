<script setup lang="ts">
import { computed, h, onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { NAlert, NButton, NDataTable, NDatePicker, NForm, NFormItem, NIcon, NInput, NInputNumber, NModal, NPagination, NSelect, NSpace, NSpin, NSwitch, NTag, useDialog, useMessage, type DataTableColumns } from 'naive-ui'
import { AddOutline, CreateOutline, TrashOutline } from '@vicons/ionicons5'
import { ApiError, del, get, http, post, put } from '../../api/http'
import type { components } from '../../api/schema'

type ExpenseCategoryRead = components['schemas']['ExpenseCategoryRead']
type ExpenseCategoryListResponse = components['schemas']['ExpenseCategoryListResponse']
type MileageDefaultsRead = components['schemas']['MileageDefaultsRead']
type MileageTransportTypeRead = components['schemas']['MileageTransportTypeRead']
type MileageTransportTypeListResponse = components['schemas']['MileageTransportTypeListResponse']
type MileageTransportTypeWrite = components['schemas']['MileageTransportTypeWrite']
type MileageRateRead = components['schemas']['MileageRateRead']
type MileageRateListResponse = components['schemas']['MileageRateListResponse']
type MileageRateWrite = components['schemas']['MileageRateWrite']
type MileageRecalculationPreviewRead = components['schemas']['MileageRecalculationPreviewRead']
type MileageRecalculationApplyRead = components['schemas']['MileageRecalculationApplyRead']

const { t } = useI18n()
const message = useMessage()
const dialog = useDialog()
const loading = ref(false)
const error = ref<string | null>(null)
const categories = ref<ExpenseCategoryRead[]>([])
const types = ref<MileageTransportTypeRead[]>([])
const rates = ref<MileageRateRead[]>([])
const defaults = ref<MileageDefaultsRead | null>(null)
const selectedCategoryId = ref<string | null>(null)
const selectedTypeId = ref<string | null>(null)
const savingDefaults = ref(false)

const typeModal = ref(false)
const editingTypeId = ref<string | null>(null)
const typeForm = ref<{ name: string; active: boolean }>({ name: '', active: true })
const savingType = ref(false)
const typeModalError = ref<string | null>(null)
const rateModal = ref(false)
const editingRateId = ref<string | null>(null)
const rateForm = ref<{ transport_type_id: string | null; effective_from: string; rate_per_km: number | null }>({ transport_type_id: null, effective_from: '', rate_per_km: null })
const savingRate = ref(false)
const rateModalError = ref<string | null>(null)

const preview = ref<MileageRecalculationPreviewRead | null>(null)
const previewLoading = ref(false)
const applying = ref(false)
const previewOffset = ref(0)
const previewLimit = 20
let previewRequestSequence = 0
let previewAbortController: AbortController | null = null

const categoryOptions = computed(() => categories.value.filter(item => item.active).map(item => ({ label: item.name, value: item.id })))
const typeOptions = computed(() => types.value.filter(item => item.active).map(item => ({ label: item.name, value: item.id })))
const generalRates = computed(() => rates.value.filter(rate => !rate.transport_type_id))
const typeRates = computed(() => rates.value.filter(rate => rate.transport_type_id))
const previewPage = computed(() => Math.floor(previewOffset.value / (preview.value?.limit ?? previewLimit)) + 1)
const previewPages = computed(() => Math.max(1, Math.ceil((preview.value?.total ?? 0) / (preview.value?.limit ?? previewLimit))))

function setError(e: unknown) { error.value = e instanceof ApiError ? e.message : String(e) }

async function load() {
  loading.value = true
  error.value = null
  try {
    const [categoryResult, typeResult, rateResult] = await Promise.all([
      get<ExpenseCategoryListResponse>('/api/v1/expense-categories'),
      get<MileageTransportTypeListResponse>('/api/v1/mileage-transport-types'),
      get<MileageRateListResponse>('/api/v1/mileage-rates'),
    ])
    categories.value = categoryResult.items
    types.value = typeResult.items
    rates.value = rateResult.items
    try {
      defaults.value = await get<MileageDefaultsRead>('/api/v1/settings/mileage-defaults')
      selectedCategoryId.value = defaults.value.expense_category_id
      selectedTypeId.value = defaults.value.default_transport_type_id
    } catch (e: unknown) {
      // A missing/deactivated/deleted setting must remain actionable: list the live choices.
      defaults.value = null
      selectedCategoryId.value = categories.value.find(item => item.active)?.id ?? null
      selectedTypeId.value = types.value.find(item => item.active)?.id ?? null
      setError(e)
    }
  } catch (e: unknown) { setError(e) } finally { loading.value = false }
}

async function saveDefaults() {
  if (!selectedCategoryId.value || !selectedTypeId.value) {
    error.value = t('mileage.settings.liveDefaultsRequired')
    return
  }
  savingDefaults.value = true
  error.value = null
  try {
    defaults.value = await put<MileageDefaultsRead>('/api/v1/settings/mileage-defaults', { expense_category_id: selectedCategoryId.value, default_transport_type_id: selectedTypeId.value })
    message.success(t('mileage.settings.defaultsSaved'))
  } catch (e: unknown) { setError(e) } finally { savingDefaults.value = false }
}

function openType(row?: MileageTransportTypeRead) {
  editingTypeId.value = row?.id ?? null
  typeForm.value = row ? { name: row.name, active: row.active ?? true } : { name: '', active: true }
  typeModalError.value = null
  typeModal.value = true
}

async function saveType() {
  if (!typeForm.value.name.trim()) { typeModalError.value = t('mileage.settings.typeNameRequired'); return }
  savingType.value = true
  typeModalError.value = null
  try {
    const body: MileageTransportTypeWrite = { name: typeForm.value.name.trim(), active: typeForm.value.active }
    if (editingTypeId.value) await put(`/api/v1/mileage-transport-types/${editingTypeId.value}`, body)
    else await post('/api/v1/mileage-transport-types', body)
    typeModal.value = false
    await load()
    message.success(t('mileage.settings.typeSaved'))
  } catch (e: unknown) { typeModalError.value = e instanceof ApiError ? e.message : String(e) } finally { savingType.value = false }
}

function removeType(row: MileageTransportTypeRead) {
  dialog.warning({ title: t('mileage.settings.deleteTypeTitle'), content: row.name, positiveText: t('common.delete'), negativeText: t('common.cancel'), onPositiveClick: async () => {
    try { await del(`/api/v1/mileage-transport-types/${row.id}`); await load(); message.success(t('mileage.settings.typeDeleted')) } catch (e: unknown) { setError(e) }
  } })
}

function openRate(row?: MileageRateRead) {
  editingRateId.value = row?.id ?? null
  rateForm.value = row ? { transport_type_id: row.transport_type_id ?? null, effective_from: row.effective_from, rate_per_km: Number(row.rate_per_km) } : { transport_type_id: null, effective_from: '', rate_per_km: null }
  rateModalError.value = null
  rateModal.value = true
}

async function saveRate() {
  if (!rateForm.value.effective_from || rateForm.value.rate_per_km === null || rateForm.value.rate_per_km <= 0) { rateModalError.value = t('mileage.settings.rateRequired'); return }
  savingRate.value = true
  rateModalError.value = null
  try {
    const body: MileageRateWrite = { transport_type_id: rateForm.value.transport_type_id, effective_from: rateForm.value.effective_from, rate_per_km: rateForm.value.rate_per_km }
    if (editingRateId.value) await put(`/api/v1/mileage-rates/${editingRateId.value}`, body)
    else await post('/api/v1/mileage-rates', body)
    rateModal.value = false
    await load()
    message.success(t('mileage.settings.rateSaved'))
  } catch (e: unknown) { rateModalError.value = e instanceof ApiError ? e.message : String(e) } finally { savingRate.value = false }
}

function removeRate(row: MileageRateRead) {
  dialog.warning({ title: t('mileage.settings.deleteRateTitle'), content: row.effective_from, positiveText: t('common.delete'), negativeText: t('common.cancel'), onPositiveClick: async () => {
    try { await del(`/api/v1/mileage-rates/${row.id}`); await load(); message.success(t('mileage.settings.rateDeleted')) } catch (e: unknown) { setError(e) }
  } })
}

async function loadPreview(offset = 0) {
  if (applying.value) return
  const requestSequence = ++previewRequestSequence
  previewAbortController?.abort()
  const controller = new AbortController()
  previewAbortController = controller
  previewLoading.value = true
  error.value = null
  try {
    const result = await http<MileageRecalculationPreviewRead>(`/api/v1/mileage-expenses/rate-recalculation/preview?limit=${previewLimit}&offset=${offset}`, { method: 'POST', body: JSON.stringify({}), signal: controller.signal })
    if (requestSequence !== previewRequestSequence) return
    preview.value = result
    // The accepted server response is the source of truth for the shown page.
    previewOffset.value = result.offset
  } catch (e: unknown) {
    if (requestSequence !== previewRequestSequence || (e instanceof DOMException && e.name === 'AbortError')) return
    setError(e)
  } finally { if (requestSequence === previewRequestSequence) previewLoading.value = false }
}

function confirmApply() {
  if (!preview.value) return
  dialog.warning({ title: t('mileage.settings.applyTitle'), content: t('mileage.settings.applyWarning', { count: preview.value.affected_count }), positiveText: t('mileage.settings.apply'), negativeText: t('common.cancel'), onPositiveClick: async () => {
    ++previewRequestSequence
    previewAbortController?.abort()
    applying.value = true
    let refreshPreview = false
    try {
      const result = await post<MileageRecalculationApplyRead>('/api/v1/mileage-expenses/rate-recalculation/apply', { preview_token: preview.value!.preview_token })
      message.success(t('mileage.settings.applied', { count: result.affected_count }))
      preview.value = null
      previewOffset.value = 0
      refreshPreview = true
    } catch (e: unknown) {
      if (e instanceof ApiError && e.status === 409) {
        preview.value = null
        previewOffset.value = 0
        error.value = t('mileage.settings.previewStale')
      } else setError(e)
    } finally { applying.value = false }
    if (refreshPreview) await loadPreview(0)
  } })
}

const typeColumns = computed<DataTableColumns<MileageTransportTypeRead>>(() => [
  { title: t('mileage.type'), key: 'name' },
  { title: t('mileage.active'), key: 'active', render: row => h(NTag, { size: 'small', type: row.active ? 'success' : 'default' }, () => row.active ? t('mileage.activeYes') : t('mileage.activeNo')) },
  { title: t('mileage.actions'), key: 'actions', render: row => h(NSpace, { size: 'small' }, () => [h(NButton, { size: 'small', quaternary: true, onClick: () => openType(row) }, () => h(NIcon, null, () => h(CreateOutline))), h(NButton, { size: 'small', quaternary: true, type: 'error', onClick: () => removeType(row) }, () => h(NIcon, null, () => h(TrashOutline)))]) },
])
const rateColumns = computed<DataTableColumns<MileageRateRead>>(() => [
  { title: t('mileage.type'), key: 'transport_type_id', render: row => row.transport_type_id ? (types.value.find(type => type.id === row.transport_type_id)?.name ?? t('mileage.inactiveHistorical')) : t('mileage.settings.generalRate') },
  { title: t('mileage.effectiveFrom'), key: 'effective_from' },
  { title: t('mileage.rate'), key: 'rate_per_km', render: row => row.rate_per_km },
  { title: t('mileage.actions'), key: 'actions', render: row => h(NSpace, { size: 'small' }, () => [h(NButton, { size: 'small', quaternary: true, onClick: () => openRate(row) }, () => h(NIcon, null, () => h(CreateOutline))), h(NButton, { size: 'small', quaternary: true, type: 'error', onClick: () => removeRate(row) }, () => h(NIcon, null, () => h(TrashOutline)))]) },
])

onMounted(() => { void load() })
onBeforeUnmount(() => {
  ++previewRequestSequence
  previewAbortController?.abort()
})
</script>

<template>
  <div class="mileage-settings">
    <h3>{{ t('mileage.settings.title') }}</h3>
    <n-alert v-if="error" type="error" closable @close="error = null" style="margin-bottom: 12px">{{ error }}</n-alert>
    <n-alert v-if="!defaults && !loading" type="warning" style="margin-bottom: 12px">{{ t('mileage.settings.defaultsUnavailable') }}</n-alert>
    <n-spin :show="loading">
      <n-form label-placement="left" label-width="150">
        <n-form-item :label="t('mileage.settings.defaultCategory')"><n-select v-model:value="selectedCategoryId" :options="categoryOptions" :placeholder="t('mileage.settings.selectLiveCategory')" /></n-form-item>
        <n-form-item :label="t('mileage.settings.defaultType')"><n-select v-model:value="selectedTypeId" :options="typeOptions" :placeholder="t('mileage.settings.selectLiveType')" /></n-form-item>
        <n-button v-if="savingDefaults" type="primary" loading>{{ t('common.save') }}</n-button>
        <n-button v-else type="primary" @click="saveDefaults">{{ t('common.save') }}</n-button>
      </n-form>

      <div class="section-header"><h4>{{ t('mileage.settings.types') }}</h4><n-button size="small" type="primary" @click="openType()"><template #icon><n-icon><AddOutline /></n-icon></template>{{ t('mileage.settings.addType') }}</n-button></div>
      <n-data-table :columns="typeColumns" :data="types" size="small" :bordered="false" />

      <div class="section-header"><h4>{{ t('mileage.settings.generalRates') }}</h4><n-button size="small" type="primary" @click="openRate()"><template #icon><n-icon><AddOutline /></n-icon></template>{{ t('mileage.settings.addRate') }}</n-button></div>
      <n-data-table :columns="rateColumns" :data="generalRates" size="small" :bordered="false" />
      <div class="section-header"><h4>{{ t('mileage.settings.typeRates') }}</h4></div>
      <n-data-table :columns="rateColumns" :data="typeRates" size="small" :bordered="false" />

      <div class="section-header"><h4>{{ t('mileage.settings.recalculation') }}</h4><n-button size="small" :disabled="applying" @click="loadPreview(0)">{{ t('mileage.settings.preview') }}</n-button></div>
      <n-spin :show="previewLoading">
        <template v-if="preview">
          <n-alert type="info" style="margin-bottom: 8px">{{ t('mileage.settings.previewSummary', { count: preview.affected_count, old: preview.old_total, next: preview.new_total, delta: preview.delta }) }}</n-alert>
          <n-data-table :data="preview.items" size="small" :bordered="false" :columns="[
            { title: t('mileage.date'), key: 'trip_date' }, { title: t('mileage.settings.oldAmount'), key: 'old_amount' }, { title: t('mileage.settings.newAmount'), key: 'new_amount' }, { title: t('mileage.settings.delta'), key: 'delta' },
          ]" />
          <n-space justify="space-between" style="margin-top: 8px"><n-pagination :page="previewPage" :page-count="previewPages" :disabled="applying" @update:page="page => loadPreview((page - 1) * (preview?.limit ?? previewLimit))" /><n-button v-if="applying" type="warning" loading>{{ t('mileage.settings.apply') }}</n-button><n-button v-else type="warning" :disabled="preview.affected_count === 0" @click="confirmApply">{{ t('mileage.settings.apply') }}</n-button></n-space>
        </template>
      </n-spin>
    </n-spin>

    <n-modal v-model:show="typeModal" preset="card" :title="editingTypeId ? t('mileage.settings.editType') : t('mileage.settings.addType')" :style="{ width: '420px' }"><n-alert v-if="typeModalError" type="error" style="margin-bottom: 12px">{{ typeModalError }}</n-alert><n-form><n-form-item :label="t('mileage.type')"><n-input v-model:value="typeForm.name" /></n-form-item><n-form-item :label="t('mileage.active')"><n-switch v-model:value="typeForm.active" /></n-form-item></n-form><template #footer><n-space justify="end"><n-button @click="typeModal = false">{{ t('common.cancel') }}</n-button><n-button v-if="savingType" type="primary" loading>{{ t('common.save') }}</n-button><n-button v-else type="primary" @click="saveType">{{ t('common.save') }}</n-button></n-space></template></n-modal>
    <n-modal v-model:show="rateModal" preset="card" :title="editingRateId ? t('mileage.settings.editRate') : t('mileage.settings.addRate')" :style="{ width: '420px' }"><n-alert v-if="rateModalError" type="error" style="margin-bottom: 12px">{{ rateModalError }}</n-alert><n-form><n-form-item :label="t('mileage.settings.rateType')"><n-select v-model:value="rateForm.transport_type_id" :options="typeOptions" clearable :placeholder="t('mileage.settings.generalRate')" /></n-form-item><n-form-item :label="t('mileage.effectiveFrom')"><n-date-picker v-model:formatted-value="rateForm.effective_from" value-format="yyyy-MM-dd" type="date" style="width: 100%" /></n-form-item><n-form-item :label="t('mileage.rate')"><n-input-number v-model:value="rateForm.rate_per_km" :min="0" :precision="3" style="width: 100%" /></n-form-item></n-form><template #footer><n-space justify="end"><n-button @click="rateModal = false">{{ t('common.cancel') }}</n-button><n-button v-if="savingRate" type="primary" loading>{{ t('common.save') }}</n-button><n-button v-else type="primary" @click="saveRate">{{ t('common.save') }}</n-button></n-space></template></n-modal>
  </div>
</template>

<style scoped>
.mileage-settings h3 { margin-top: 0; }
.section-header { display: flex; justify-content: space-between; align-items: center; margin-top: 20px; }
.section-header h4 { margin: 0 0 8px; }
</style>
