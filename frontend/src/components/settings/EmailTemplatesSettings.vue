<script setup lang="ts">
/**
 * Email templates settings — company-level editable email templates.
 *
 * Supports: invoice × {en, zh} and quote × {en, zh}.
 * Each template has a subject + body (plain text + placeholders).
 * Loads from / saves to GET/PUT /settings/email-templates.
 *
 * The form shows a placeholder reference below each body textarea so the
 * author knows which tokens are available.
 */
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { get, put } from '../../api/http'
import type { components } from '../../api/schema'

type EmailTemplatesRead = components['schemas']['EmailTemplatesRead']
type EmailTemplate = components['schemas']['EmailTemplate']
type EmailTemplateLocaleMap = components['schemas']['EmailTemplateLocaleMap']

const { t } = useI18n()

// ── reactive state ──────────────────────────────────────────────────────────

const loading = ref(false)
const saving = ref(false)
const message = ref('')
const messageType = ref<'success' | 'error'>('success')

// Active tab: doc_type × locale
const activeDocType = ref<'invoice' | 'quote' | 'advance' | 'final' | 'credit_note' | 'refund'>('invoice')
const activeLocale = ref<'en' | 'zh'>('en')

// Templates state (mirrors the API shape)
type TemplateMap = {
  invoice: { en: EmailTemplate; zh: EmailTemplate }
  quote: { en: EmailTemplate; zh: EmailTemplate }
  advance: { en: EmailTemplate; zh: EmailTemplate }
  final: { en: EmailTemplate; zh: EmailTemplate }
  credit_note: { en: EmailTemplate; zh: EmailTemplate }
  refund: { en: EmailTemplate; zh: EmailTemplate }
}

const templates = ref<TemplateMap>({
  invoice: {
    en: { subject: '', body: '' },
    zh: { subject: '', body: '' },
  },
  quote: {
    en: { subject: '', body: '' },
    zh: { subject: '', body: '' },
  },
  advance: { en: { subject: '', body: '' }, zh: { subject: '', body: '' } },
  final: { en: { subject: '', body: '' }, zh: { subject: '', body: '' } },
  credit_note: { en: { subject: '', body: '' }, zh: { subject: '', body: '' } },
  refund: { en: { subject: '', body: '' }, zh: { subject: '', body: '' } },
})

// ── helpers ─────────────────────────────────────────────────────────────────

function fromLocaleMap(lm: EmailTemplateLocaleMap): { en: EmailTemplate; zh: EmailTemplate } {
  return {
    en: { subject: lm.en?.subject ?? '', body: lm.en?.body ?? '' },
    zh: { subject: lm.zh?.subject ?? '', body: lm.zh?.body ?? '' },
  }
}

// ── lifecycle ────────────────────────────────────────────────────────────────

onMounted(async () => {
  loading.value = true
  try {
    const data = await get<EmailTemplatesRead>('/api/v1/settings/email-templates')
    templates.value = {
      invoice: fromLocaleMap(data.invoice),
      quote: fromLocaleMap(data.quote),
      advance: fromLocaleMap(data.advance),
      final: fromLocaleMap(data.final),
      credit_note: fromLocaleMap(data.credit_note),
      refund: fromLocaleMap(data.refund),
    }
  } catch {
    // keep blank defaults on error
  } finally {
    loading.value = false
  }
})

// ── current template accessor (two-way) ─────────────────────────────────────

function currentTemplate(): EmailTemplate {
  return templates.value[activeDocType.value][activeLocale.value]
}

function updateSubject(val: string) {
  templates.value[activeDocType.value][activeLocale.value].subject = val
}

function updateBody(val: string) {
  templates.value[activeDocType.value][activeLocale.value].body = val
}

// ── save ─────────────────────────────────────────────────────────────────────

async function handleSave() {
  saving.value = true
  message.value = ''
  try {
    await put<EmailTemplatesRead>('/api/v1/settings/email-templates', {
      invoice: templates.value.invoice,
      quote: templates.value.quote,
      advance: templates.value.advance,
      final: templates.value.final,
      credit_note: templates.value.credit_note,
      refund: templates.value.refund,
    })
    message.value = t('settings.emailTemplates.saveSuccess')
    messageType.value = 'success'
  } catch (e: unknown) {
    message.value = e instanceof Error ? e.message : t('settings.emailTemplates.saveFailed')
    messageType.value = 'error'
  } finally {
    saving.value = false
  }
}

// ── placeholder reference lists ───────────────────────────────────────────────

const COMMON_PLACEHOLDERS = [
  '{COMPANY_NAME}',
  '{CUSTOMER_NAME}',
  '{DATE}',
  '{DOCUMENT_NUMBER}',
  '{TOTAL}',
  '{CURRENCY}',
]

const INVOICE_PLACEHOLDERS = [
  '{INVOICE_NUMBER}',
  '{DUE_DATE}',
  '{AMOUNT_DUE}',
]

const CREDIT_PLACEHOLDERS = ['{CREDIT_NOTE_NUMBER}', '{SOURCE_DOCUMENT_NUMBER}']

const QUOTE_PLACEHOLDERS = [
  '{QUOTE_NUMBER}',
  '{VALID_UNTIL}',
]

function activePlaceholders(): string[] {
  const extra = activeDocType.value === 'quote'
    ? QUOTE_PLACEHOLDERS
    : activeDocType.value === 'credit_note' || activeDocType.value === 'refund'
      ? CREDIT_PLACEHOLDERS
      : INVOICE_PLACEHOLDERS
  return [...COMMON_PLACEHOLDERS, ...extra]
}
</script>

<template>
  <n-spin :show="loading">
    <n-h4 style="margin: 0 0 12px">{{ t('settings.emailTemplates.title') }}</n-h4>
    <n-text depth="3" style="font-size: 12px; display: block; margin-bottom: 16px">
      {{ t('settings.emailTemplates.hint') }}
    </n-text>

    <!-- Doc type tabs -->
    <n-tabs v-model:value="activeDocType" type="line" size="small" style="margin-bottom: 8px">
      <n-tab-pane name="invoice" :tab="t('settings.emailTemplates.docTypeInvoice')" />
      <n-tab-pane name="quote" :tab="t('settings.emailTemplates.docTypeQuote')" />
      <n-tab-pane name="advance" :tab="t('settings.emailTemplates.docTypeAdvance')" />
      <n-tab-pane name="final" :tab="t('settings.emailTemplates.docTypeFinal')" />
      <n-tab-pane name="credit_note" :tab="t('settings.emailTemplates.docTypeCreditNote')" />
      <n-tab-pane name="refund" :tab="t('settings.emailTemplates.docTypeRefund')" />
    </n-tabs>

    <!-- Locale tabs -->
    <n-tabs v-model:value="activeLocale" type="segment" size="small" style="margin-bottom: 16px">
      <n-tab-pane name="en" tab="English" />
      <n-tab-pane name="zh" tab="中文" />
    </n-tabs>

    <n-form label-placement="top">
      <!-- Subject -->
      <n-form-item :label="t('settings.emailTemplates.subject')">
        <n-input
          :value="currentTemplate().subject"
          :placeholder="t('settings.emailTemplates.subjectPlaceholder')"
          @update:value="updateSubject"
        />
      </n-form-item>

      <!-- Body -->
      <n-form-item :label="t('settings.emailTemplates.body')">
        <n-input
          :value="currentTemplate().body"
          type="textarea"
          :rows="8"
          :placeholder="t('settings.emailTemplates.bodyPlaceholder')"
          @update:value="updateBody"
        />
      </n-form-item>

      <!-- Placeholder reference -->
      <n-form-item :label="t('settings.emailTemplates.placeholders')">
        <div>
          <n-text depth="3" style="font-size: 12px; line-height: 1.8">
            {{ activePlaceholders().join('  ') }}
          </n-text>
          <div style="margin-top: 4px">
            <n-text depth="3" style="font-size: 11px">
              {{ t('settings.emailTemplates.placeholderHint') }}
            </n-text>
          </div>
        </div>
      </n-form-item>
    </n-form>

    <n-alert
      v-if="message"
      :type="messageType"
      closable
      style="margin-bottom: 12px"
      @close="message = ''"
    >
      {{ message }}
    </n-alert>

    <n-button type="primary" :loading="saving" @click="handleSave">
      {{ t('settings.emailTemplates.save') }}
    </n-button>
  </n-spin>
</template>
