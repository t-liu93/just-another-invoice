<script setup lang="ts">
/**
 * DocumentSendDialog – send dialog for Invoice / Quote emails.
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

type EmailTemplatesRead = components['schemas']['EmailTemplatesRead']
type DocumentDefaultsRead = components['schemas']['DocumentDefaultsRead']
type EmailLogRead = components['schemas']['EmailLogRead']
type DocumentSendRequest = components['schemas']['DocumentSendRequest']

const props = defineProps<{
  show: boolean
  /** 'invoice' | 'quote' */
  docType: 'invoice' | 'quote'
  /** document id */
  docId: string
  /** pre-fill to field */
  customerEmail: string | null | undefined
  /** customer.locale — may be null/undefined meaning "follow company default" */
  customerLocale: 'en' | 'zh' | null | undefined
}>()

const emit = defineEmits<{
  (e: 'update:show', v: boolean): void
  (e: 'sent', log: EmailLogRead): void
}>()

const { t } = useI18n()
const message = useMessage()

// ---- state ----
const templates = ref<EmailTemplatesRead | null>(null)
const companyDefaultLocale = ref<'en' | 'zh'>('en')
const loading = ref(false)
const sending = ref(false)
const sendError = ref<string | null>(null)

// Form fields
const formTo = ref('')
const formCc = ref('')
const formLocale = ref<'en' | 'zh'>('en')
const formSubject = ref('')
const formBody = ref('')

// ---- locale options ----
const localeOptions = computed(() => [
  { label: t('pdf.localeEn'), value: 'en' },
  { label: t('pdf.localeZh'), value: 'zh' },
])

// ---- compute D2 chain default locale (frontend portion) ----
function resolveDefaultLocale(): 'en' | 'zh' {
  if (props.customerLocale === 'en' || props.customerLocale === 'zh') {
    return props.customerLocale
  }
  return companyDefaultLocale.value
}

// ---- fill subject/body from templates when locale or docType changes ----
function fillFromTemplate(locale: 'en' | 'zh') {
  if (!templates.value) return
  const tpl = templates.value[props.docType][locale]
  formSubject.value = tpl.subject
  formBody.value = tpl.body
}

watch(formLocale, (newLocale) => {
  fillFromTemplate(newLocale)
})

// ---- load templates + defaults when dialog opens ----
async function loadData() {
  loading.value = true
  sendError.value = null
  try {
    const [tpl, defaults] = await Promise.all([
      get<EmailTemplatesRead>('/api/v1/settings/email-templates'),
      get<DocumentDefaultsRead>('/api/v1/settings/document-defaults'),
    ])
    templates.value = tpl
    companyDefaultLocale.value = defaults.locale ?? 'en'

    // Pre-fill form
    formTo.value = props.customerEmail ?? ''
    formCc.value = ''
    formLocale.value = resolveDefaultLocale()
    fillFromTemplate(formLocale.value)
  } catch (e: unknown) {
    message.error(e instanceof Error ? e.message : String(e))
  } finally {
    loading.value = false
  }
}

watch(() => props.show, (v) => {
  if (v) loadData()
})

// ---- send ----
async function handleSend() {
  if (!formTo.value.trim()) {
    message.warning(t('sendDialog.toRequired'))
    return
  }
  sending.value = true
  sendError.value = null
  try {
    const body: DocumentSendRequest = {
      to: formTo.value.trim(),
      cc: formCc.value.trim() || null,
      locale: formLocale.value,
      subject: formSubject.value.trim() || undefined,
      body: formBody.value.trim() || undefined,
    }
    const endpointBase = props.docType === 'invoice' ? '/api/v1/invoices' : '/api/v1/quotes'
    const log = await post<EmailLogRead>(`${endpointBase}/${props.docId}/send`, body)
    emit('sent', log)
    emit('update:show', false)
    message.success(t('sendDialog.sendSuccess'))
  } catch (e: unknown) {
    sendError.value = e instanceof Error ? e.message : String(e)
  } finally {
    sending.value = false
  }
}

function handleClose() {
  emit('update:show', false)
}
</script>

<template>
  <n-modal
    :show="show"
    preset="card"
    :title="t('sendDialog.title')"
    style="max-width: 560px"
    :closable="!sending"
    @update:show="(v: boolean) => emit('update:show', v)"
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
