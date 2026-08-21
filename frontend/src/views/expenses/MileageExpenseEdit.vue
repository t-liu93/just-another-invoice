<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { NAlert, NButton, NCard, NCheckbox, NDatePicker, NForm, NFormItem, NIcon, NInput, NInputNumber, NSelect, NSpace, NSpin, NText, useMessage } from 'naive-ui'
import { ArrowBackOutline } from '@vicons/ionicons5'
import { ApiError, get } from '../../api/http'
import { localDateStr } from '../../utils/date'
import { useMileageStore, type MileageCalculationRead } from '../../stores/mileage'
import type { components } from '../../api/schema'

type MileageTransportTypeListResponse = components['schemas']['MileageTransportTypeListResponse']
type MileageTransportTypeRead = components['schemas']['MileageTransportTypeRead']
type MileageDefaultsRead = components['schemas']['MileageDefaultsRead']
type CalculationInput = { trip_date: string; transport_type_id: string; one_way_distance_km: number; round_trip: boolean }

const { t } = useI18n()
const message = useMessage()
const route = useRoute()
const router = useRouter()
const store = useMileageStore()
const tripId = computed(() => route.params.id as string | undefined)
const isEdit = computed(() => Boolean(tripId.value))
const loading = ref(false)
const saving = ref(false)
const loadError = ref<string | null>(null)
const saveError = ref<string | null>(null)
const calculationError = ref<string | null>(null)
const types = ref<MileageTransportTypeRead[]>([])
const tripDate = ref(localDateStr(new Date()))
const transportTypeId = ref<string | null>(null)
const oneWayDistanceKm = ref<number | null>(null)
const roundTrip = ref(false)
const originAddress = ref('')
const destinationAddress = ref('')
const purpose = ref('')
const note = ref('')
// A persisted record is evidence, distinct from a fresh calculation that may be
// saved.  Keeping these separate lets an initial verification failure remain
// readable without making the old amount eligible for another PUT.
const persistedCalculation = ref<MileageCalculationRead | null>(null)
const calculation = ref<MileageCalculationRead | null>(null)
const acceptedCalculationInput = ref<CalculationInput | null>(null)
const historicalTypeName = ref<string | null>(null)
const calculationLoading = ref(false)
let calculateTimer: ReturnType<typeof setTimeout> | null = null
let calculationRequestSequence = 0
let calculationAbortController: AbortController | null = null
let hydrating = false

function currentCalculationInput(): CalculationInput | null {
  if (!tripDate.value || !transportTypeId.value || oneWayDistanceKm.value === null || oneWayDistanceKm.value <= 0) return null
  return { trip_date: tripDate.value, transport_type_id: transportTypeId.value, one_way_distance_km: oneWayDistanceKm.value, round_trip: roundTrip.value }
}
function sameCalculationInput(left: CalculationInput | null, right: CalculationInput | null) { return JSON.stringify(left) === JSON.stringify(right) }
const selectedTypeIsLive = computed(() => Boolean(transportTypeId.value && types.value.some(item => item.id === transportTypeId.value && item.active)))
const historicalTypeNeedsReplacement = computed(() => isEdit.value && !selectedTypeIsLive.value)
const calculationStale = computed(() => {
  const input = currentCalculationInput()
  return !input || !acceptedCalculationInput.value || !sameCalculationInput(acceptedCalculationInput.value, input)
})
const canSave = computed(() => Boolean(currentCalculationInput()) && !saving.value && !calculationLoading.value && !calculationStale.value && Boolean(calculation.value) && !historicalTypeNeedsReplacement.value)
const typeOptions = computed(() => {
  const current = types.value.find(item => item.id === transportTypeId.value)
  const live = types.value.filter(item => item.active)
  if (current && !current.active) return [{ label: `${current.name} (${t('mileage.inactiveHistorical')})`, value: current.id, disabled: true }, ...live.map(item => ({ label: item.name, value: item.id }))]
  return live.map(item => ({ label: item.name, value: item.id }))
})
function errorMessage(e: unknown) { return e instanceof ApiError ? e.message : String(e) }
async function loadTypes() { types.value = (await get<MileageTransportTypeListResponse>('/api/v1/mileage-transport-types')).items }
function cancelCalculation() {
  ++calculationRequestSequence
  calculationAbortController?.abort()
  calculationAbortController = null
  calculationLoading.value = false
}
async function recompute() {
  const input = currentCalculationInput()
  if (!input) return
  const requestSequence = ++calculationRequestSequence
  calculationAbortController?.abort()
  const controller = new AbortController()
  calculationAbortController = controller
  calculationLoading.value = true
  calculationError.value = null
  try {
    const result = await store.calculate(input, controller.signal)
    if (requestSequence !== calculationRequestSequence || !sameCalculationInput(input, currentCalculationInput())) return
    calculation.value = result
    acceptedCalculationInput.value = input
  } catch (e: unknown) {
    if (requestSequence !== calculationRequestSequence || (e instanceof DOMException && e.name === 'AbortError')) return
    calculation.value = null
    acceptedCalculationInput.value = null
    calculationError.value = errorMessage(e)
  } finally { if (requestSequence === calculationRequestSequence) calculationLoading.value = false }
}
function queueRecompute() {
  if (calculateTimer) clearTimeout(calculateTimer)
  calculateTimer = setTimeout(() => { void recompute() }, 250)
}
function invalidateCalculation() {
  cancelCalculation()
  if (calculateTimer) clearTimeout(calculateTimer)
  // Once a raw calculation field changes, its loaded snapshot no longer
  // describes the draft and must not be shown as a current preview.
  persistedCalculation.value = null
  calculation.value = null
  acceptedCalculationInput.value = null
  calculationError.value = null
  queueRecompute()
}
function fill(item: components['schemas']['MileageExpenseRead'], calculationIsCurrent = false) {
  hydrating = true
  try {
    tripDate.value = item.trip_date
    transportTypeId.value = item.transport_type_id ?? null
    oneWayDistanceKm.value = Number(item.one_way_distance_km)
    roundTrip.value = item.round_trip ?? false
    originAddress.value = item.origin_address ?? ''
    destinationAddress.value = item.destination_address ?? ''
    purpose.value = item.purpose ?? ''
    note.value = item.note ?? ''
    historicalTypeName.value = item.transport_type_name
    const snapshot = { one_way_distance_km: item.one_way_distance_km, total_distance_km: item.total_distance_km, rate_rule_id: item.rate_rule_id ?? '', rate_effective_from: item.rate_effective_from, rate_per_km: item.rate_per_km, amount: item.amount, currency: item.currency }
    persistedCalculation.value = calculationIsCurrent ? null : snapshot
    calculation.value = calculationIsCurrent ? snapshot : null
    acceptedCalculationInput.value = calculationIsCurrent ? currentCalculationInput() : null
  } finally {
    hydrating = false
  }
}
onMounted(async () => {
  loading.value = true
  try {
    await loadTypes()
    if (tripId.value) {
      fill(await store.getMileage(tripId.value))
      // Preserve the persisted snapshot while a live type is independently checked.
      if (selectedTypeIsLive.value) queueRecompute()
    } else {
      try { transportTypeId.value = (await get<MileageDefaultsRead>('/api/v1/settings/mileage-defaults')).default_transport_type_id } catch (e: unknown) { loadError.value = errorMessage(e) }
    }
  } catch (e: unknown) { loadError.value = errorMessage(e) } finally { loading.value = false }
})
watch([tripDate, transportTypeId, oneWayDistanceKm, roundTrip], () => {
  if (!hydrating) invalidateCalculation()
}, { flush: 'sync' })
onBeforeUnmount(() => { if (calculateTimer) clearTimeout(calculateTimer); cancelCalculation() })
async function save() {
  if (historicalTypeNeedsReplacement.value) { saveError.value = t('mileage.historicalTypeReplacement', { type: historicalTypeName.value ?? t('mileage.inactiveHistorical') }); return }
  if (!currentCalculationInput()) { saveError.value = t('mileage.validationError'); return }
  if (!canSave.value) { saveError.value = t('mileage.previewRequired'); return }
  saving.value = true
  saveError.value = null
  try {
    const body = { trip_date: tripDate.value, transport_type_id: transportTypeId.value, one_way_distance_km: oneWayDistanceKm.value!, round_trip: roundTrip.value, origin_address: originAddress.value || null, destination_address: destinationAddress.value || null, purpose: purpose.value || null, note: note.value || null }
    if (tripId.value) await store.updateMileage(tripId.value, body)
    else await store.createMileage(body)
    message.success(t('mileage.saveSuccess'))
    await router.push({ path: '/expenses', query: { tab: 'mileage' } })
  } catch (e: unknown) { saveError.value = errorMessage(e) } finally { saving.value = false }
}
</script>

<template>
  <div class="mileage-edit-container">
    <div class="page-header"><n-button text @click="router.push({ path: '/expenses', query: { tab: 'mileage' } })"><template #icon><n-icon><ArrowBackOutline /></n-icon></template>{{ t('mileage.backToList') }}</n-button><h2>{{ isEdit ? t('mileage.edit') : t('mileage.new') }}</h2></div>
    <n-spin :show="loading">
      <n-alert v-if="loadError" type="error" closable style="margin-bottom: 16px" @close="loadError = null">{{ loadError }}</n-alert>
      <n-alert v-if="calculationError" type="error" closable style="margin-bottom: 16px" @close="calculationError = null">{{ calculationError }}</n-alert>
      <n-alert v-if="saveError" type="error" closable style="margin-bottom: 16px" @close="saveError = null">{{ saveError }}</n-alert>
      <n-alert v-if="historicalTypeNeedsReplacement" type="warning" style="margin-bottom: 16px">{{ t('mileage.historicalTypeReplacement', { type: historicalTypeName ?? t('mileage.inactiveHistorical') }) }}</n-alert>
      <div class="edit-layout">
        <n-form class="form-col" label-placement="left" label-width="145" @submit.prevent="save">
          <n-form-item :label="t('mileage.date')"><n-date-picker v-model:formatted-value="tripDate" value-format="yyyy-MM-dd" type="date" style="width: 100%" /></n-form-item>
          <n-form-item :label="t('mileage.type')"><n-select v-model:value="transportTypeId" :options="typeOptions" :placeholder="t('mileage.typePlaceholder')" /></n-form-item>
          <n-form-item :label="t('mileage.oneWay')"><n-input-number v-model:value="oneWayDistanceKm" :min="0" :precision="3" style="width: 100%"><template #suffix>km</template></n-input-number></n-form-item>
          <n-form-item :label="t('mileage.roundTrip')"><n-checkbox v-model:checked="roundTrip">{{ t('mileage.roundTripHint') }}</n-checkbox></n-form-item>
          <n-form-item :label="t('mileage.origin')"><n-input v-model:value="originAddress" :placeholder="t('mileage.optionalPlainText')" /></n-form-item>
          <n-form-item :label="t('mileage.destination')"><n-input v-model:value="destinationAddress" :placeholder="t('mileage.optionalPlainText')" /></n-form-item>
          <n-form-item :label="t('mileage.purpose')"><n-input v-model:value="purpose" :placeholder="t('mileage.optionalPlainText')" /></n-form-item>
          <n-form-item :label="t('mileage.note')"><n-input v-model:value="note" type="textarea" :rows="3" :placeholder="t('mileage.optionalPlainText')" /></n-form-item>
          <n-space><n-button type="primary" attr-type="submit" :loading="saving" :disabled="!canSave">{{ t('common.save') }}</n-button><n-button @click="router.push({ path: '/expenses', query: { tab: 'mileage' } })">{{ t('common.cancel') }}</n-button></n-space>
        </n-form>
        <div class="preview-col"><n-card :title="t('mileage.authoritativePreview')" size="small"><n-spin :show="calculationLoading"><template v-if="calculation && !calculationStale"><p><n-text depth="3">{{ t('mileage.totalDistance') }}</n-text><br><strong>{{ calculation.total_distance_km }} km</strong></p><p><n-text depth="3">{{ t('mileage.rate') }}</n-text><br><strong>{{ calculation.rate_per_km }} {{ calculation.currency }}/km</strong></p><p><n-text depth="3">{{ t('mileage.amount') }}</n-text><br><strong>{{ calculation.amount }} {{ calculation.currency }}</strong></p><n-text depth="3">{{ t('mileage.backendCalculated') }}</n-text></template><template v-else-if="persistedCalculation"><p><n-text depth="3">{{ t('mileage.historicalPreview') }}</n-text><br><strong>{{ t('mileage.type') }}: {{ historicalTypeName ?? t('mileage.inactiveHistorical') }}</strong></p><p><n-text depth="3">{{ t('mileage.totalDistance') }}</n-text><br><strong>{{ persistedCalculation.total_distance_km }} km</strong></p><p><n-text depth="3">{{ t('mileage.rate') }}</n-text><br><strong>{{ persistedCalculation.rate_per_km }} {{ persistedCalculation.currency }}/km</strong></p><p><n-text depth="3">{{ t('mileage.amount') }}</n-text><br><strong>{{ persistedCalculation.amount }} {{ persistedCalculation.currency }}</strong></p><n-text depth="3">{{ t('mileage.previewRefreshing') }}</n-text></template><n-text v-else depth="3">{{ t('mileage.previewHint') }}</n-text></n-spin></n-card><n-alert type="info" style="margin-top: 12px">{{ t('mileage.noReceiptRequired') }}</n-alert></div>
      </div>
    </n-spin>
  </div>
</template>

<style scoped>
.mileage-edit-container { max-width: 1000px; margin: 0 auto; padding: 24px; }
.page-header { display: flex; align-items: center; gap: 16px; margin-bottom: 18px; }.page-header h2 { margin: 0; }
.edit-layout { display: grid; grid-template-columns: minmax(0, 1fr) 290px; gap: 24px; }.preview-col { min-width: 0; }
@media (max-width: 760px) { .edit-layout { grid-template-columns: 1fr; } }
</style>
