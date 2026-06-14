<script setup lang="ts">
/**
 * InvoicePaymentPanel – shows the payment aggregate for a single invoice,
 * allows recording new payments and editing/deleting existing ones.
 *
 * Design rules (M7 §步骤4):
 * - All amounts/status come exclusively from the backend aggregate (InvoicePaymentsResponse).
 *   Frontend never locally computes due_amount or paid_status.
 * - Form only sends raw input (payment_date, amount, payment_method_id, note, reference).
 * - DRAFT/CANCELLED invoices: panel is visible but record-payment form is hidden
 *   with an info alert telling the user to mark the invoice as sent first.
 * - Loading-prop + v-if bug avoidance:
 *   Action buttons that need a :loading prop use v-if/v-else to swap between
 *   a static "loading disabled" button and the real clickable button, so that
 *   production vite build does not silently drop @click (see memory: vue-loading-prop-vif-prod-bug).
 */

import { ref, computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  useMessage, useDialog,
  NCard, NSpace, NTag, NText, NButton, NForm, NFormItem,
  NInputNumber, NSelect, NInput, NDatePicker, NAlert, NSpin,
  NDivider, NIcon, NEmpty, NDropdown,
} from 'naive-ui'
import { AddOutline, CreateOutline, TrashOutline, DownloadOutline } from '@vicons/ionicons5'
import { usePaymentsStore } from '../stores/payments'
import type { InvoicePaymentsResponse, PaymentRead } from '../stores/payments'
import { get, downloadBlob } from '../api/http'
import type { components } from '../api/schema'
import { localDateStr } from '../utils/date'

type PaymentMethodRead = components['schemas']['PaymentMethodRead']
type PaymentMethodListResponse = components['schemas']['PaymentMethodListResponse']

const props = defineProps<{
  invoiceId: string
  invoiceStatus: string
}>()

const emit = defineEmits<{
  (e: 'paymentsChanged', aggregate: InvoicePaymentsResponse): void
}>()

const { t } = useI18n()
const message = useMessage()
const dialog = useDialog()
const store = usePaymentsStore()

// ---- state ----
const aggregate = ref<InvoicePaymentsResponse | null>(null)
const panelLoading = ref(false)
const paymentMethods = ref<PaymentMethodRead[]>([])

// Form fields – only raw inputs per M7 design
const formDate = ref(localDateStr(new Date()))
const formAmount = ref<number | null>(null)
const formMethodId = ref<string | null>(null)
const formNote = ref<string | null>(null)
const formReference = ref<string | null>(null)
const showForm = ref(false)

// Edit state
const editingPayment = ref<PaymentRead | null>(null)
const editDate = ref('')
const editAmount = ref<number | null>(null)
const editMethodId = ref<string | null>(null)
const editNote = ref<string | null>(null)
const editReference = ref<string | null>(null)

// Separate saving flags for record vs edit to avoid shared state
const recordSaving = ref(false)
const editSaving = ref(false)
const deleteSaving = ref<string | null>(null) // stores the payment id being deleted

// ---- Receipt PDF download ----
const receiptDownloadingId = ref<string | null>(null)

function receiptPdfLocaleOptions(paymentId: string) {
  return [
    { label: t('pdf.localeDefault'), key: `${paymentId}:default` },
    { label: t('pdf.localeEn'), key: `${paymentId}:en` },
    { label: t('pdf.localeZh'), key: `${paymentId}:zh` },
  ]
}

async function handleDownloadReceipt(paymentId: string, locale?: 'en' | 'zh') {
  receiptDownloadingId.value = paymentId
  try {
    const url = locale
      ? `/api/v1/payments/${paymentId}/receipt-pdf?locale=${locale}`
      : `/api/v1/payments/${paymentId}/receipt-pdf`
    await downloadBlob(url, `receipt-${paymentId}.pdf`)
  } catch (e: unknown) {
    message.error(e instanceof Error ? e.message : t('pdf.downloadFailed'))
  } finally {
    receiptDownloadingId.value = null
  }
}

function handleReceiptLocaleSelect(key: string) {
  const [id, locale] = key.split(':')
  if (locale === 'default') {
    handleDownloadReceipt(id)
  } else {
    handleDownloadReceipt(id, locale as 'en' | 'zh')
  }
}

// ---- computed ----
const canRecord = computed(() =>
  props.invoiceStatus === 'SENT' || props.invoiceStatus === 'COMPLETED',
)

const paymentMethodOptions = computed(() =>
  paymentMethods.value
    .filter(m => m.active)
    .map(m => ({ label: m.name, value: m.id })),
)

const fmtMoney = (v: string | number) => Number(v).toFixed(2)

function paidStatusType(status: string): 'default' | 'warning' | 'success' {
  if (status === 'PAID') return 'success'
  if (status === 'PARTIALLY_PAID') return 'warning'
  return 'default'
}

// ---- load ----
async function loadPaymentMethods() {
  const res = await get<PaymentMethodListResponse>('/api/v1/payment-methods')
  paymentMethods.value = res.items
}

async function loadAggregate() {
  panelLoading.value = true
  try {
    aggregate.value = await store.listInvoicePayments(props.invoiceId)
  } catch (e: unknown) {
    message.error(e instanceof Error ? e.message : String(e))
  } finally {
    panelLoading.value = false
  }
}

onMounted(async () => {
  await Promise.all([loadAggregate(), loadPaymentMethods()])
})

// ---- record payment ----
function openForm() {
  formDate.value = localDateStr(new Date())
  formAmount.value = null
  formMethodId.value = null
  formNote.value = null
  formReference.value = null
  showForm.value = true
}

function cancelForm() {
  showForm.value = false
}

async function handleRecord() {
  if (!formAmount.value || formAmount.value <= 0) {
    message.warning(t('payments.amountRequired'))
    return
  }
  recordSaving.value = true
  try {
    const result = await store.recordPayment(props.invoiceId, {
      payment_date: formDate.value,
      amount: formAmount.value,
      payment_method_id: formMethodId.value,
      note: formNote.value,
      reference: formReference.value,
    })
    aggregate.value = result
    emit('paymentsChanged', result)
    showForm.value = false
    message.success(t('payments.recordSuccess'))
  } catch (e: unknown) {
    message.error(e instanceof Error ? e.message : t('payments.recordFailed'))
  } finally {
    recordSaving.value = false
  }
}

// ---- edit payment ----
function openEdit(payment: PaymentRead) {
  editingPayment.value = payment
  editDate.value = payment.payment_date
  editAmount.value = Number(payment.amount)
  editMethodId.value = payment.payment_method_id ?? null
  editNote.value = payment.note ?? null
  editReference.value = payment.reference ?? null
}

function cancelEdit() {
  editingPayment.value = null
}

async function handleEdit() {
  if (!editingPayment.value) return
  if (!editAmount.value || editAmount.value <= 0) {
    message.warning(t('payments.amountRequired'))
    return
  }
  editSaving.value = true
  try {
    const result = await store.updatePayment(editingPayment.value.id, {
      payment_date: editDate.value,
      amount: editAmount.value,
      payment_method_id: editMethodId.value,
      note: editNote.value,
      reference: editReference.value,
    })
    aggregate.value = result
    emit('paymentsChanged', result)
    editingPayment.value = null
    message.success(t('payments.updateSuccess'))
  } catch (e: unknown) {
    message.error(e instanceof Error ? e.message : t('payments.updateFailed'))
  } finally {
    editSaving.value = false
  }
}

// ---- delete payment ----
function handleDelete(payment: PaymentRead) {
  dialog.warning({
    title: t('payments.deleteTitle'),
    content: t('payments.deleteConfirm'),
    positiveText: t('payments.delete'),
    negativeText: t('common.cancel'),
    onPositiveClick: async () => {
      deleteSaving.value = payment.id
      try {
        // DELETE returns 200 + InvoicePaymentsResponse (M7 contract)
        const result = await store.deletePayment(payment.id)
        aggregate.value = result
        emit('paymentsChanged', result)
        message.success(t('payments.deleteSuccess'))
      } catch (e: unknown) {
        message.error(e instanceof Error ? e.message : t('payments.deleteFailed'))
      } finally {
        deleteSaving.value = null
      }
    },
  })
}
</script>

<template>
  <n-card :title="t('payments.panelTitle')" style="margin-bottom: 16px">
    <!-- Panel header: aggregate badges -->
    <template #header-extra>
      <n-space v-if="aggregate" align="center" size="small">
        <n-tag :type="paidStatusType(aggregate.paid_status)" size="small">
          {{ t(`payments.paidStatus.${aggregate.paid_status}`) }}
        </n-tag>
        <n-text depth="3" style="font-size: 13px">
          {{ t('payments.paidTotal') }}: {{ fmtMoney(aggregate.paid_total) }}
          &nbsp;|&nbsp;
          {{ t('payments.dueAmount') }}: {{ fmtMoney(aggregate.due_amount) }}
        </n-text>
      </n-space>
    </template>

    <n-spin :show="panelLoading">

      <!-- Guard: invoice not yet sent -->
      <n-alert
        v-if="!canRecord"
        type="info"
        style="margin-bottom: 12px"
      >
        {{ t('payments.needSentStatus') }}
      </n-alert>

      <!-- Existing payment items -->
      <div v-if="aggregate && aggregate.items && aggregate.items.length > 0">
        <div
          v-for="payment in aggregate.items"
          :key="payment.id"
          class="payment-row"
        >
          <!-- Edit mode for this row -->
          <template v-if="editingPayment && editingPayment.id === payment.id">
            <n-form label-placement="left" label-width="90px" style="margin: 8px 0">
              <n-form-item :label="t('payments.paymentDate')">
                <n-date-picker
                  v-model:formatted-value="editDate"
                  value-format="yyyy-MM-dd"
                  type="date"
                  clearable
                  style="width: 160px"
                />
              </n-form-item>
              <n-form-item :label="t('payments.amount')">
                <n-input-number
                  v-model:value="editAmount"
                  :min="0.001"
                  :precision="3"
                  style="width: 160px"
                />
              </n-form-item>
              <n-form-item :label="t('payments.paymentMethod')">
                <n-select
                  v-model:value="editMethodId"
                  :options="paymentMethodOptions"
                  :placeholder="t('payments.methodOptional')"
                  clearable
                  style="width: 200px"
                />
              </n-form-item>
              <n-form-item :label="t('payments.reference')">
                <n-input v-model:value="editReference" clearable style="width: 200px" />
              </n-form-item>
              <n-form-item :label="t('payments.note')">
                <n-input v-model:value="editNote" type="textarea" :autosize="{ minRows: 1 }" clearable style="width: 200px" />
              </n-form-item>
            </n-form>
            <n-space size="small" style="margin-bottom: 8px">
              <!-- Save edit button: use v-if/v-else to avoid loading-prop+v-if prod bug -->
              <n-button v-if="editSaving" size="small" type="primary" loading disabled>
                {{ t('payments.save') }}
              </n-button>
              <n-button v-else size="small" type="primary" @click="handleEdit">
                {{ t('payments.save') }}
              </n-button>
              <n-button size="small" @click="cancelEdit">{{ t('common.cancel') }}</n-button>
            </n-space>
            <n-divider style="margin: 8px 0" />
          </template>

          <!-- Display mode for this row -->
          <template v-else>
            <div class="payment-item">
              <div class="payment-item-main">
                <n-text strong>{{ fmtMoney(payment.amount) }}</n-text>
                <n-text depth="3" style="margin-left: 8px; font-size: 13px">
                  {{ new Date(payment.payment_date).toLocaleDateString() }}
                </n-text>
                <n-text v-if="payment.payment_method_name" depth="3" style="margin-left: 8px; font-size: 13px">
                  · {{ payment.payment_method_name }}
                </n-text>
                <n-text v-if="payment.reference" depth="3" style="margin-left: 8px; font-size: 13px">
                  · Ref: {{ payment.reference }}
                </n-text>
                <n-text v-if="payment.note" depth="3" style="margin-left: 8px; font-size: 13px">
                  · {{ payment.note }}
                </n-text>
              </div>
              <n-space size="small" :wrap-item="false" class="payment-item-actions">
                <!-- Receipt PDF download dropdown (always visible, not gated by canRecord) -->
                <n-dropdown
                  :options="receiptPdfLocaleOptions(payment.id)"
                  trigger="click"
                  @select="handleReceiptLocaleSelect"
                >
                  <n-button
                    size="small"
                    quaternary
                    circle
                    :title="t('payments.downloadReceipt')"
                    :disabled="receiptDownloadingId === payment.id"
                  >
                    <template #icon><n-icon><DownloadOutline /></n-icon></template>
                  </n-button>
                </n-dropdown>

                <template v-if="canRecord">
                  <n-button
                    size="small"
                    quaternary
                    circle
                    :title="t('payments.edit')"
                    @click="openEdit(payment)"
                  >
                    <template #icon><n-icon><CreateOutline /></n-icon></template>
                  </n-button>
                  <!-- Delete button: no dynamic prop, no loading state here; deletion dialog handles it -->
                  <n-button
                    size="small"
                    quaternary
                    circle
                    type="error"
                    :title="t('payments.delete')"
                    :disabled="deleteSaving === payment.id"
                    @click="handleDelete(payment)"
                  >
                    <template #icon><n-icon><TrashOutline /></n-icon></template>
                  </n-button>
                </template>
              </n-space>
            </div>
            <n-divider v-if="aggregate && aggregate.items && aggregate.items.indexOf(payment) < aggregate.items.length - 1" style="margin: 6px 0" />
          </template>
        </div>
      </div>

      <n-empty
        v-else-if="aggregate && (!aggregate.items || aggregate.items.length === 0)"
        :description="t('payments.noPayments')"
        style="padding: 12px 0 16px"
        size="small"
      />

      <!-- Record new payment form -->
      <template v-if="canRecord">
        <template v-if="showForm && !editingPayment">
          <n-divider style="margin: 12px 0" />
          <n-form label-placement="left" label-width="90px">
            <n-form-item :label="t('payments.paymentDate')" required>
              <n-date-picker
                v-model:formatted-value="formDate"
                value-format="yyyy-MM-dd"
                type="date"
                clearable
                style="width: 160px"
              />
            </n-form-item>
            <n-form-item :label="t('payments.amount')" required>
              <n-input-number
                v-model:value="formAmount"
                :min="0.001"
                :precision="3"
                :placeholder="t('payments.amountPlaceholder')"
                style="width: 160px"
              />
            </n-form-item>
            <n-form-item :label="t('payments.paymentMethod')">
              <n-select
                v-model:value="formMethodId"
                :options="paymentMethodOptions"
                :placeholder="t('payments.methodOptional')"
                clearable
                style="width: 200px"
              />
            </n-form-item>
            <n-form-item :label="t('payments.reference')">
              <n-input v-model:value="formReference" :placeholder="t('payments.referencePlaceholder')" clearable style="width: 200px" />
            </n-form-item>
            <n-form-item :label="t('payments.note')">
              <n-input v-model:value="formNote" type="textarea" :autosize="{ minRows: 1 }" :placeholder="t('payments.notePlaceholder')" clearable style="width: 200px" />
            </n-form-item>
          </n-form>
          <n-space size="small">
            <!-- Use v-if/v-else pattern to avoid loading-prop+v-if prod bug (see vue-loading-prop-vif-prod-bug memory) -->
            <n-button v-if="recordSaving" size="small" type="primary" loading disabled>
              {{ t('payments.record') }}
            </n-button>
            <n-button v-else size="small" type="primary" @click="handleRecord">
              {{ t('payments.record') }}
            </n-button>
            <n-button size="small" @click="cancelForm">{{ t('common.cancel') }}</n-button>
          </n-space>
        </template>

        <div v-else-if="!editingPayment" style="margin-top: 12px">
          <n-button dashed size="small" @click="openForm">
            <template #icon><n-icon><AddOutline /></n-icon></template>
            {{ t('payments.addPayment') }}
          </n-button>
        </div>
      </template>

    </n-spin>
  </n-card>
</template>

<style scoped>
.payment-row {
  padding: 2px 0;
}

.payment-item {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  padding: 6px 0;
  gap: 8px;
  flex-wrap: wrap;
}

.payment-item-main {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 0;
  min-width: 0;
  flex: 1 1 auto;
}

.payment-item-actions {
  flex-shrink: 0;
}
</style>
