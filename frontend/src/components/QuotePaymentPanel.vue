<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import {
  NAlert,
  NButton,
  NCard,
  NDatePicker,
  NDivider,
  NDropdown,
  NEmpty,
  NForm,
  NFormItem,
  NIcon,
  NInput,
  NInputNumber,
  NSelect,
  NSpace,
  NSpin,
  NTag,
  NText,
  useDialog,
  useMessage,
} from 'naive-ui'
import {
  AddOutline,
  CreateOutline,
  DownloadOutline,
  EyeOutline,
  SendOutline,
  TrashOutline,
} from '@vicons/ionicons5'
import { downloadBlob, get } from '../api/http'
import type { components } from '../api/schema'
import { usePaymentsStore } from '../stores/payments'
import type { PaymentRead, QuotePaymentsResponse } from '../stores/payments'
import { formatDate, localDateStr } from '../utils/date'
import {
  hasCurrentQuotePayment,
  isPaymentMutationBusy,
  isCurrentQuotePaymentContext,
} from '../utils/quotePaymentPanelState'
import PdfPreviewDialog from './PdfPreviewDialog.vue'
import DocumentSendDialog from './DocumentSendDialog.vue'
import { openReceiptDialog } from '../utils/receiptEmail'

type PaymentMethodRead = components['schemas']['PaymentMethodRead']
type PaymentMethodListResponse = components['schemas']['PaymentMethodListResponse']

const props = defineProps<{
  quoteId: string
  quoteStatus: string
  convertedInvoiceId?: string | null
  customerEmail?: string | null
  customerLocale?: 'en' | 'zh' | null
  canRecordPayment?: boolean
}>()

const emit = defineEmits<{
  (e: 'paymentsChanged', aggregate: QuotePaymentsResponse): void
  (e: 'receiptSent', log: components['schemas']['EmailLogRead']): void
}>()

const { t } = useI18n()
const router = useRouter()
const message = useMessage()
const dialog = useDialog()
const store = usePaymentsStore()

const aggregate = ref<QuotePaymentsResponse | null>(null)
const paymentMethods = ref<PaymentMethodRead[]>([])
const aggregateLoading = ref(false)
const methodsLoading = ref(false)
const aggregateLoaded = ref(false)
const methodsLoaded = ref(false)
const aggregateError = ref<string | null>(null)
const methodsError = ref<string | null>(null)
const showForm = ref(false)
const recordSaving = ref(false)
const editSaving = ref(false)
const deleteSaving = ref<string | null>(null)
const editingPayment = ref<PaymentRead | null>(null)

const formDate = ref(localDateStr(new Date()))
const formAmount = ref<number | null>(null)
const formMethodId = ref<string | null>(null)
const formReference = ref<string | null>(null)
const formNote = ref<string | null>(null)

const editDate = ref('')
const editAmount = ref<number | null>(null)
const editMethodId = ref<string | null>(null)
const editReference = ref<string | null>(null)
const editNote = ref<string | null>(null)
const receiptSendPaymentId = ref<string | null>(null)
const receiptSendShow = ref(false)
const receiptSending = ref(false)

let contextVersion = 0
let aggregateRequestVersion = 0
let methodsRequestVersion = 0

const panelLoading = computed(() => aggregateLoading.value || methodsLoading.value)
const aggregateItems = computed(() => aggregate.value?.items ?? [])
const mutationBusy = computed(() => isPaymentMutationBusy(
  recordSaving.value,
  editSaving.value,
  deleteSaving.value,
))

const quoteAllowsMutations = computed(() =>
  props.canRecordPayment !== false
  &&
  props.quoteStatus === 'ACCEPTED'
  && !props.convertedInvoiceId
  && !aggregate.value?.converted_invoice_id
)

const canMutate = computed(() =>
  quoteAllowsMutations.value
  && aggregateLoaded.value
  && methodsLoaded.value
  && !aggregateError.value
  && !methodsError.value
  && !mutationBusy.value,
)

const paymentMethodOptions = computed(() => paymentMethods.value
  .filter(method => method.active)
  .map(method => ({ label: method.name, value: method.id })))

const fmtMoney = (value: string | number) => Number(value).toFixed(2)

function errorText(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback
}

function isCurrentContext(quoteId: string, version: number): boolean {
  return isCurrentQuotePaymentContext(quoteId, props.quoteId, version, contextVersion)
}

function isCurrentPayment(paymentId: string): boolean {
  return hasCurrentQuotePayment(paymentId, aggregateItems.value)
}

function openReceiptSend(paymentId: string) {
  const next = openReceiptDialog(paymentId, receiptSendPaymentId.value, receiptSending.value)
  if (!next) return
  receiptSendPaymentId.value = next.paymentId
  receiptSendShow.value = next.show
}

function handleReceiptSent(log: components['schemas']['EmailLogRead']) {
  emit('receiptSent', log)
}

function resetFormState() {
  showForm.value = false
  editingPayment.value = null
  formDate.value = localDateStr(new Date())
  formAmount.value = null
  formMethodId.value = null
  formReference.value = null
  formNote.value = null
  editDate.value = ''
  editAmount.value = null
  editMethodId.value = null
  editReference.value = null
  editNote.value = null
}

function resetForQuoteChange() {
  contextVersion += 1
  aggregateRequestVersion += 1
  aggregate.value = null
  aggregateLoaded.value = false
  aggregateError.value = null
  aggregateLoading.value = false
  recordSaving.value = false
  editSaving.value = false
  deleteSaving.value = null
  resetFormState()
}

async function loadAggregate(quoteId = props.quoteId) {
  const requestVersion = ++aggregateRequestVersion
  const requestContextVersion = contextVersion
  aggregateLoading.value = true
  aggregateError.value = null
  try {
    const result = await store.listQuotePayments(quoteId)
    if (!isCurrentContext(quoteId, requestContextVersion) || requestVersion !== aggregateRequestVersion) return
    aggregate.value = result
    aggregateLoaded.value = true
  } catch (error: unknown) {
    if (!isCurrentContext(quoteId, requestContextVersion) || requestVersion !== aggregateRequestVersion) return
    aggregateError.value = errorText(error, t('payments.loadQuotePaymentsFailed'))
  } finally {
    if (isCurrentContext(quoteId, requestContextVersion) && requestVersion === aggregateRequestVersion) {
      aggregateLoading.value = false
    }
  }
}

async function loadMethods() {
  const requestVersion = ++methodsRequestVersion
  methodsLoading.value = true
  methodsError.value = null
  try {
    const response = await get<PaymentMethodListResponse>('/api/v1/payment-methods')
    if (requestVersion !== methodsRequestVersion) return
    paymentMethods.value = response.items
    methodsLoaded.value = true
  } catch (error: unknown) {
    if (requestVersion !== methodsRequestVersion) return
    methodsError.value = errorText(error, t('payments.loadPaymentMethodsFailed'))
  } finally {
    if (requestVersion === methodsRequestVersion) methodsLoading.value = false
  }
}

watch(() => props.quoteId, quoteId => {
  resetForQuoteChange()
  void loadAggregate(quoteId)
}, { immediate: true })

onMounted(() => {
  void loadMethods()
})

function openForm() {
  if (!canMutate.value) return
  formDate.value = localDateStr(new Date())
  formAmount.value = null
  formMethodId.value = null
  formReference.value = null
  formNote.value = null
  showForm.value = true
}

async function recordPayment() {
  const quoteId = props.quoteId
  const actionContextVersion = contextVersion
  if (!canMutate.value || !isCurrentContext(quoteId, actionContextVersion)) return
  if (!formAmount.value || formAmount.value <= 0) {
    message.warning(t('payments.amountRequired'))
    return
  }
  recordSaving.value = true
  try {
    const result = await store.recordQuotePayment(quoteId, {
      payment_date: formDate.value,
      amount: formAmount.value,
      payment_method_id: formMethodId.value,
      reference: formReference.value,
      note: formNote.value,
    })
    if (isCurrentContext(quoteId, actionContextVersion)) {
      aggregate.value = result
      emit('paymentsChanged', result)
      showForm.value = false
      message.success(t('payments.recordSuccess'))
    }
  } catch (error: unknown) {
    if (isCurrentContext(quoteId, actionContextVersion)) {
      message.error(errorText(error, t('payments.recordFailed')))
    }
  } finally {
    if (isCurrentContext(quoteId, actionContextVersion)) recordSaving.value = false
  }
}

function openEdit(payment: PaymentRead) {
  if (!canMutate.value || !isCurrentPayment(payment.id)) return
  editingPayment.value = payment
  editDate.value = payment.payment_date
  editAmount.value = Number(payment.amount)
  editMethodId.value = payment.payment_method_id ?? null
  editReference.value = payment.reference ?? null
  editNote.value = payment.note ?? null
}

async function updatePayment() {
  const quoteId = props.quoteId
  const actionContextVersion = contextVersion
  const payment = editingPayment.value
  if (!canMutate.value || !payment || !isCurrentPayment(payment.id)) return
  if (!editAmount.value || editAmount.value <= 0) {
    message.warning(t('payments.amountRequired'))
    return
  }
  editSaving.value = true
  try {
    const result = await store.updatePayment(payment.id, {
      payment_date: editDate.value,
      amount: editAmount.value,
      payment_method_id: editMethodId.value,
      reference: editReference.value,
      note: editNote.value,
    })
    if (!result.quote) throw new Error(t('payments.missingQuoteAggregate'))
    if (isCurrentContext(quoteId, actionContextVersion)) {
      aggregate.value = result.quote
      emit('paymentsChanged', result.quote)
      editingPayment.value = null
      message.success(t('payments.updateSuccess'))
    }
  } catch (error: unknown) {
    if (isCurrentContext(quoteId, actionContextVersion)) {
      message.error(errorText(error, t('payments.updateFailed')))
    }
  } finally {
    if (isCurrentContext(quoteId, actionContextVersion)) editSaving.value = false
  }
}

function deletePayment(payment: PaymentRead) {
  const quoteId = props.quoteId
  const actionContextVersion = contextVersion
  if (!canMutate.value || !isCurrentPayment(payment.id)) return
  dialog.warning({
    title: t('payments.deleteTitle'),
    content: t('payments.deleteConfirm'),
    positiveText: t('payments.delete'),
    negativeText: t('common.cancel'),
    onPositiveClick: async () => {
      if (
        !canMutate.value
        || !isCurrentContext(quoteId, actionContextVersion)
        || !isCurrentPayment(payment.id)
      ) {
        message.warning(t('payments.stalePaymentAction'))
        return
      }
      deleteSaving.value = payment.id
      try {
        const result = await store.deletePayment(payment.id)
        if (!result.quote) throw new Error(t('payments.missingQuoteAggregate'))
        if (isCurrentContext(quoteId, actionContextVersion)) {
          aggregate.value = result.quote
          emit('paymentsChanged', result.quote)
          message.success(t('payments.deleteSuccess'))
        }
      } catch (error: unknown) {
        if (isCurrentContext(quoteId, actionContextVersion)) {
          message.error(errorText(error, t('payments.deleteFailed')))
        }
      } finally {
        if (isCurrentContext(quoteId, actionContextVersion)) deleteSaving.value = null
      }
    },
  })
}

function receiptOptions(paymentId: string) {
  return [
    { label: t('pdf.localeDefault'), key: `${paymentId}:default` },
    { label: t('pdf.localeEn'), key: `${paymentId}:en` },
    { label: t('pdf.localeZh'), key: `${paymentId}:zh` },
  ]
}

const receiptPreviewShow = ref(false)
const receiptPreviewSrc = ref<string | null>(null)
const receiptPreviewFallback = ref('receipt.pdf')

function receiptUrl(paymentId: string, locale: string) {
  return locale === 'default'
    ? `/api/v1/payments/${paymentId}/receipt-pdf`
    : `/api/v1/payments/${paymentId}/receipt-pdf?locale=${locale}`
}

function previewReceipt(key: string) {
  const [paymentId, locale] = key.split(':')
  receiptPreviewSrc.value = receiptUrl(paymentId, locale)
  receiptPreviewFallback.value = `receipt-${paymentId}.pdf`
  receiptPreviewShow.value = true
}

async function downloadReceipt(key: string) {
  const [paymentId, locale] = key.split(':')
  try {
    await downloadBlob(receiptUrl(paymentId, locale), `receipt-${paymentId}.pdf`)
  } catch (error: unknown) {
    message.error(error instanceof Error ? error.message : t('pdf.downloadFailed'))
  }
}
</script>

<template>
  <n-card :title="t('payments.quotePanelTitle')" style="margin-bottom: 16px">
    <template #header-extra>
      <n-space v-if="aggregate" size="small" align="center" wrap>
        <n-tag size="small">
          {{ t('payments.quoteTotal') }}: {{ fmtMoney(aggregate.total_incl_vat) }}
        </n-tag>
        <n-tag type="info" size="small">
          {{ t('payments.paidTotal') }}: {{ fmtMoney(aggregate.paid_total) }}
        </n-tag>
        <n-tag type="warning" size="small">
          {{ t('payments.remainingAmount') }}: {{ fmtMoney(aggregate.remaining_amount) }}
        </n-tag>
      </n-space>
    </template>

    <n-spin :show="panelLoading">
      <n-alert
        v-if="aggregateError"
        type="error"
        :title="t('payments.loadQuotePaymentsFailed')"
        style="margin-bottom: 12px"
      >
        <n-space vertical size="small">
          <span>{{ aggregateError }}</span>
          <n-button size="small" @click="loadAggregate()">
            {{ t('common.retry') }}
          </n-button>
        </n-space>
      </n-alert>
      <n-alert
        v-if="methodsError && quoteStatus === 'ACCEPTED' && !convertedInvoiceId"
        type="error"
        :title="t('payments.loadPaymentMethodsFailed')"
        style="margin-bottom: 12px"
      >
        <n-space vertical size="small">
          <span>{{ methodsError }}</span>
          <n-button size="small" @click="loadMethods">
            {{ t('common.retry') }}
          </n-button>
        </n-space>
      </n-alert>
      <n-alert
        v-if="aggregate?.converted_invoice_id || convertedInvoiceId"
        type="info"
        style="margin-bottom: 12px"
      >
        {{ t('payments.convertedReadOnly') }}
        <n-button
          text
          type="primary"
          @click="router.push(`/invoices/${aggregate?.converted_invoice_id || convertedInvoiceId}/edit`)"
        >
          {{ t('payments.viewFinalInvoice') }}
        </n-button>
      </n-alert>
      <n-alert v-else-if="quoteStatus !== 'ACCEPTED'" type="info" style="margin-bottom: 12px">
        {{ t('payments.quoteMustBeAccepted') }}
      </n-alert>

      <div v-if="aggregateLoaded && aggregate?.items?.length">
        <div v-for="payment in aggregate.items" :key="payment.id" class="payment-row">
          <template v-if="editingPayment?.id === payment.id">
            <n-form class="payment-form" label-placement="top">
              <n-form-item :label="t('payments.paymentDate')">
                <n-date-picker
                  v-model:formatted-value="editDate"
                  value-format="yyyy-MM-dd"
                  type="date"
                  class="payment-form-control"
                />
              </n-form-item>
              <n-form-item :label="t('payments.amount')">
                <n-input-number v-model:value="editAmount" :min="0.01" :precision="2" class="payment-form-control" />
              </n-form-item>
              <n-form-item :label="t('payments.paymentMethod')">
                <n-select v-model:value="editMethodId" :options="paymentMethodOptions" clearable class="payment-form-control" />
              </n-form-item>
              <n-form-item :label="t('payments.reference')">
                <n-input v-model:value="editReference" clearable class="payment-form-control" />
              </n-form-item>
              <n-form-item :label="t('payments.note')">
                <n-input v-model:value="editNote" type="textarea" clearable class="payment-form-control" />
              </n-form-item>
            </n-form>
            <n-space wrap>
              <n-button v-if="editSaving" size="small" type="primary" loading disabled>
                {{ t('payments.save') }}
              </n-button>
              <n-button v-else size="small" type="primary" :disabled="!canMutate" @click="updatePayment">
                {{ t('payments.save') }}
              </n-button>
              <n-button size="small" @click="editingPayment = null">{{ t('common.cancel') }}</n-button>
            </n-space>
          </template>
          <div v-else class="payment-item">
            <div class="payment-main">
              <n-text strong>{{ fmtMoney(payment.amount) }}</n-text>
              <n-text depth="3">{{ formatDate(payment.payment_date) }}</n-text>
              <n-text v-if="payment.payment_method_name" depth="3">{{ payment.payment_method_name }}</n-text>
              <n-text v-if="payment.reference" depth="3">{{ payment.reference }}</n-text>
            </div>
            <n-space size="small" :wrap-item="false">
              <n-dropdown :options="receiptOptions(payment.id)" @select="previewReceipt">
                <n-button size="small" quaternary circle :title="t('pdf.preview')">
                  <template #icon><n-icon><EyeOutline /></n-icon></template>
                </n-button>
              </n-dropdown>
              <n-dropdown :options="receiptOptions(payment.id)" @select="downloadReceipt">
                <n-button size="small" quaternary circle :title="t('payments.downloadReceipt')">
                  <template #icon><n-icon><DownloadOutline /></n-icon></template>
                </n-button>
              </n-dropdown>
              <n-button
                size="small"
                quaternary
                circle
                :title="t('payments.sendReceipt')"
                :disabled="receiptSending"
                @click="openReceiptSend(payment.id)"
              >
                <template #icon><n-icon><SendOutline /></n-icon></template>
              </n-button>
              <template v-if="quoteAllowsMutations">
                <n-button size="small" quaternary circle :disabled="!canMutate" @click="openEdit(payment)">
                  <template #icon><n-icon><CreateOutline /></n-icon></template>
                </n-button>
                <n-button
                  v-if="deleteSaving === payment.id"
                  size="small"
                  quaternary
                  circle
                  type="error"
                  loading
                  disabled
                >
                  <template #icon><n-icon><TrashOutline /></n-icon></template>
                </n-button>
                <n-button
                  v-else
                  size="small"
                  quaternary
                  circle
                  type="error"
                  :disabled="!canMutate"
                  @click="deletePayment(payment)"
                >
                  <template #icon><n-icon><TrashOutline /></n-icon></template>
                </n-button>
              </template>
            </n-space>
          </div>
          <n-divider />
        </div>
      </div>
      <n-empty
        v-else-if="aggregateLoaded"
        :description="t('payments.noPayments')"
        size="small"
      />

      <template v-if="quoteAllowsMutations">
        <template v-if="showForm && !editingPayment">
          <n-form class="payment-form" label-placement="top" style="margin-top: 16px">
            <n-form-item :label="t('payments.paymentDate')" required>
              <n-date-picker
                v-model:formatted-value="formDate"
                value-format="yyyy-MM-dd"
                type="date"
                class="payment-form-control"
              />
            </n-form-item>
            <n-form-item :label="t('payments.amount')" required>
              <n-input-number v-model:value="formAmount" :min="0.01" :precision="2" class="payment-form-control" />
            </n-form-item>
            <n-form-item :label="t('payments.paymentMethod')">
              <n-select v-model:value="formMethodId" :options="paymentMethodOptions" clearable class="payment-form-control" />
            </n-form-item>
            <n-form-item :label="t('payments.reference')">
              <n-input v-model:value="formReference" clearable class="payment-form-control" />
            </n-form-item>
            <n-form-item :label="t('payments.note')">
              <n-input v-model:value="formNote" type="textarea" clearable class="payment-form-control" />
            </n-form-item>
          </n-form>
          <n-space wrap>
            <n-button v-if="recordSaving" size="small" type="primary" loading disabled>
              {{ t('payments.record') }}
            </n-button>
            <n-button v-else size="small" type="primary" :disabled="!canMutate" @click="recordPayment">
              {{ t('payments.record') }}
            </n-button>
            <n-button size="small" @click="showForm = false">{{ t('common.cancel') }}</n-button>
          </n-space>
        </template>
        <n-button
          v-else-if="!editingPayment"
          dashed
          size="small"
          style="margin-top: 12px"
          :disabled="!canMutate"
          @click="openForm"
        >
          <template #icon><n-icon><AddOutline /></n-icon></template>
          {{ t('payments.addQuotePayment') }}
        </n-button>
      </template>
    </n-spin>
  </n-card>

  <PdfPreviewDialog
    v-model:show="receiptPreviewShow"
    :src="receiptPreviewSrc"
    :fallback-filename="receiptPreviewFallback"
  />
  <DocumentSendDialog
    v-if="receiptSendPaymentId"
    v-model:show="receiptSendShow"
    v-model:sending="receiptSending"
    doc-type="receipt"
    :doc-id="receiptSendPaymentId"
    :customer-email="customerEmail"
    :customer-locale="customerLocale"
    @sent="handleReceiptSent"
  />
</template>

<style scoped>
.payment-row:last-child :deep(.n-divider) {
  display: none;
}

.payment-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.payment-main {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.payment-form {
  max-width: 100%;
}

.payment-form :deep(.n-form-item-blank) {
  min-width: 0;
}

.payment-form-control {
  width: 100%;
}

@media (max-width: 480px) {
  .payment-form :deep(.n-form-item) {
    margin-bottom: 12px;
  }
}
</style>
