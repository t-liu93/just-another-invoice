<script setup lang="ts">
/**
 * DocumentSendDialog – send dialog for Invoice, Quote, and payment-receipt emails.
 *
 * Pre-fills:
 *   - to: customer.email
 *   - locale: customer.locale || companyDefault || "en"  (D2 chain, frontend only
 *             computes the default selection; actual D2 resolution happens on the backend)
 *   - subject / body: raw template text from GET /api/v1/settings/email-templates
 *     (placeholders like {COMPANY_NAME} are shown as-is; backend renders them at send time)
 *
 * Loading-prop + v-if prod bug avoidance:
 *   The Send button uses v-if/v-else to swap between a disabled loading button
 *   and the real clickable one (see memory: vue-loading-prop-vif-prod-bug).
 */

import { ref, watch, computed } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  useMessage,
  NModal, NForm, NFormItem, NInput, NSelect, NSpace, NButton,
  NAlert, NSpin,
} from 'naive-ui'
import { get, post } from '../api/http'
import type { components } from '../api/schema'
import { useDocumentSendContext } from '../composables/useDocumentSendContext'

type EmailTemplatesRead = components['schemas']['EmailTemplatesRead']
type DocumentDefaultsRead = components['schemas']['DocumentDefaultsRead']
type EmailLogRead = components['schemas']['EmailLogRead']
type DocumentSendRequest = components['schemas']['DocumentSendRequest']

const props = defineProps<{
  show: boolean
  /** 'invoice' | 'quote' | 'receipt' */
  docType: 'invoice' | 'quote' | 'receipt'
  /** document id */
  docId: string
  /** pre-fill to field */
  customerEmail: string | null | undefined
  /** customer.locale — may be null/undefined meaning "follow company default" */
  customerLocale: 'en' | 'zh' | null | undefined
}>()

const emit = defineEmits<{
  (e: 'update:show', v: boolean): void
  (e: 'update:sending', v: boolean): void
  (e: 'sent', log: EmailLogRead): void
}>()

const { t } = useI18n()
const message = useMessage()

// ---- state ----
const templates = ref<EmailTemplatesRead | null>(null)
const companyDefaultLocale = ref<'en' | 'zh'>('en')
const loading = ref(false)
const sendError = ref<string | null>(null)

// Form fields
const formTo = ref('')
const formCc = ref('')
const formLocale = ref<'en' | 'zh'>('en')
const formSubject = ref('')
const formBody = ref('')

const receiptDefaults: Record<'en' | 'zh', { subject: string; body: string }> = {
  en: {
    subject: 'Payment receipt for {DOCUMENT_NUMBER} from {COMPANY_NAME}',
    body: 'Dear {CUSTOMER_NAME},\n\nPlease find the payment receipt for {DOCUMENT_NUMBER} attached.\n\nKind regards,\n{COMPANY_NAME}',
  },
  zh: {
    subject: '{COMPANY_NAME} 的收款收据（{DOCUMENT_NUMBER}）',
    body: '尊敬的 {CUSTOMER_NAME}：\n\n随信附上 {DOCUMENT_NUMBER} 的收款收据。\n\n此致\n{COMPANY_NAME}',
  },
}

// ---- locale options ----
const localeOptions = computed(() => [
  { label: t('pdf.localeEn'), value: 'en' },
  { label: t('pdf.localeZh'), value: 'zh' },
])

// ---- fill subject/body from templates when locale or docType changes ----
function fillFromTemplate(locale: 'en' | 'zh') {
  if (props.docType === 'receipt') {
    formSubject.value = receiptDefaults[locale].subject
    formBody.value = receiptDefaults[locale].body
    return
  }
  if (!templates.value) return
  const tpl = templates.value[props.docType][locale]
  formSubject.value = tpl.subject
  formBody.value = tpl.body
}

watch(formLocale, (newLocale) => {
  fillFromTemplate(newLocale)
})

function resetForm() {
  templates.value = null
  companyDefaultLocale.value = 'en'
  formTo.value = ''
  formCc.value = ''
  formLocale.value = 'en'
  formSubject.value = ''
  formBody.value = ''
  sendError.value = null
}

// ---- load templates + defaults whenever the visible document context changes ----
const dialogContext = useDocumentSendContext(
  () => ({
    show: props.show,
    docType: props.docType,
    docId: props.docId,
    customerEmail: props.customerEmail,
    customerLocale: props.customerLocale,
  }),
  resetForm,
  async (context, isCurrent) => {
  loading.value = true
  try {
    const [tpl, defaults] = await Promise.all([
      context.docType === 'receipt'
        ? Promise.resolve(null)
        : get<EmailTemplatesRead>('/api/v1/settings/email-templates'),
      get<DocumentDefaultsRead>('/api/v1/settings/document-defaults'),
    ])
    if (!isCurrent()) return
    templates.value = tpl
    companyDefaultLocale.value = defaults.locale ?? 'en'

    // Pre-fill form
    formTo.value = context.customerEmail ?? ''
    formCc.value = ''
    formLocale.value = context.customerLocale === 'en' || context.customerLocale === 'zh'
      ? context.customerLocale
      : companyDefaultLocale.value
    fillFromTemplate(formLocale.value)
  } catch (e: unknown) {
    if (isCurrent()) message.error(e instanceof Error ? e.message : String(e))
  } finally {
    if (isCurrent()) loading.value = false
  }
  },
)
const { sending } = dialogContext

// The payment panels consume this real state, rather than assuming sends are
// idle while deciding whether another receipt context may open.
watch(sending, value => emit('update:sending', value), { flush: 'sync' })

// ---- send ----
async function handleSend() {
  if (!formTo.value.trim()) {
    message.warning(t('sendDialog.toRequired'))
    return
  }
  const frozen = dialogContext.beginSend()
  if (!frozen) return
  sendError.value = null
  let closeCurrentContext = false
  try {
    const body: DocumentSendRequest = {
      to: formTo.value.trim(),
      cc: formCc.value.trim() || null,
      locale: formLocale.value,
      subject: formSubject.value.trim() || undefined,
      body: formBody.value.trim() || undefined,
    }
    const endpoint = frozen.context.docType === 'receipt'
      ? `/api/v1/payments/${frozen.context.docId}/send-receipt`
      : `${frozen.context.docType === 'invoice' ? '/api/v1/invoices' : '/api/v1/quotes'}/${frozen.context.docId}/send`
    const log = await post<EmailLogRead>(endpoint, body)
    emit('sent', log)
    closeCurrentContext = true
    message.success(t('sendDialog.sendSuccess'))
  } catch (e: unknown) {
    sendError.value = e instanceof Error ? e.message : String(e)
  } finally {
    // A stale completion must not close a replacement document context.
    if (dialogContext.finishSend(frozen) && closeCurrentContext) {
      emit('update:show', false)
    }
  }
}

function handleClose() {
  if (sending.value) return
  emit('update:show', false)
}

function handleModalShow(value: boolean) {
  if (!value && sending.value) return
  emit('update:show', value)
}
</script>

<template>
  <n-modal
    :show="show"
    preset="card"
    :title="t('sendDialog.title')"
    style="max-width: 560px"
    :closable="!sending"
    @update:show="handleModalShow"
  >
    <n-spin :show="loading">
      <n-form v-if="!loading" label-placement="top">
        <n-form-item :label="t('sendDialog.to')" required>
          <n-input
            v-model:value="formTo"
            :placeholder="t('sendDialog.toPlaceholder')"
            :disabled="sending"
          />
        </n-form-item>

        <n-form-item :label="t('sendDialog.cc')">
          <n-input
            v-model:value="formCc"
            :placeholder="t('sendDialog.ccPlaceholder')"
            :disabled="sending"
          />
        </n-form-item>

        <n-form-item :label="t('sendDialog.locale')" required>
          <n-select
            v-model:value="formLocale"
            :options="localeOptions"
            :disabled="sending"
            style="width: 160px"
          />
        </n-form-item>

        <n-form-item :label="t('sendDialog.subject')">
          <n-input
            v-model:value="formSubject"
            :placeholder="t('sendDialog.subjectPlaceholder')"
            :disabled="sending"
          />
        </n-form-item>

        <n-form-item :label="t('sendDialog.body')">
          <n-input
            v-model:value="formBody"
            type="textarea"
            :autosize="{ minRows: 5, maxRows: 12 }"
            :placeholder="t('sendDialog.bodyPlaceholder')"
            :disabled="sending"
          />
        </n-form-item>

        <n-alert v-if="sendError" type="error" style="margin-bottom: 12px">
          {{ sendError }}
        </n-alert>
      </n-form>
    </n-spin>

    <template #footer>
      <n-space justify="end">
        <n-button :disabled="sending" @click="handleClose">
          {{ t('common.cancel') }}
        </n-button>
        <!-- v-if/v-else pattern to avoid loading-prop+v-if prod bug -->
        <n-button v-if="sending" type="primary" loading disabled>
          {{ t('sendDialog.send') }}
        </n-button>
        <n-button v-else type="primary" @click="handleSend">
          {{ t('sendDialog.send') }}
        </n-button>
      </n-space>
    </template>
  </n-modal>
</template>
