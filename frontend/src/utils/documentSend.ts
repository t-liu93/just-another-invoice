import type { components } from '../api/schema'

type InvoiceDocumentKind = components['schemas']['InvoiceDocumentKind']
type DocumentSendRequest = components['schemas']['DocumentSendRequest']

export type DocumentSendType =
  | 'invoice'
  | 'advance'
  | 'final'
  | 'credit_note'
  | 'quote'
  | 'receipt'
  | 'refund'

export type DocumentTemplateKey = Exclude<DocumentSendType, 'receipt'>

export interface EmailTemplateValues {
  subject: string
  body: string
}

/** Map the API kind to the matching typed email-template field. */
export function invoiceDocumentSendType(
  kind: InvoiceDocumentKind | null | undefined,
): 'invoice' | 'advance' | 'final' | 'credit_note' {
  switch (kind) {
    case 'ADVANCE':
      return 'advance'
    case 'FINAL':
      return 'final'
    case 'CREDIT_NOTE':
      return 'credit_note'
    case 'STANDARD':
    default:
      return 'invoice'
  }
}

export function isFormalInvoiceSendType(type: DocumentSendType): boolean {
  return type === 'invoice'
    || type === 'advance'
    || type === 'final'
    || type === 'credit_note'
    || type === 'refund'
}

/**
 * Formal output starts from the locale frozen at issue time.  This is only a
 * display baseline: callers still omit the locale unless the user changes it.
 * Quote and receipt callers retain M9's live customer/company resolution.
 */
export function resolveDocumentSendInitialLocale(input: {
  type: DocumentSendType
  snapshotLocale: 'en' | 'zh' | null | undefined
  fallbackLocale: 'en' | 'zh'
}): 'en' | 'zh' {
  if (isFormalInvoiceSendType(input.type) && input.snapshotLocale) {
    return input.snapshotLocale
  }
  return input.snapshotLocale ?? input.fallbackLocale
}

/**
 * Formal documents deliberately omit untouched fields.  That leaves the
 * server to select the issue-snapshot locale and matching kind template;
 * only an actual user edit becomes an explicit override.  Quote and receipt
 * callers retain their established M9 payload behaviour.
 */
export function buildDocumentSendPayload(input: {
  type: DocumentSendType
  to: string
  cc: string
  locale: 'en' | 'zh'
  localeWasChosen: boolean
  subject: string
  body: string
  template: EmailTemplateValues
}): DocumentSendRequest {
  const payload: DocumentSendRequest = {
    to: input.to.trim(),
    cc: input.cc.trim() || null,
  }
  if (!isFormalInvoiceSendType(input.type)) {
    return {
      ...payload,
      locale: input.locale,
      subject: input.subject.trim() || undefined,
      body: input.body.trim() || undefined,
    }
  }
  if (input.localeWasChosen) payload.locale = input.locale
  if (input.subject !== input.template.subject) {
    payload.subject = input.subject.trim() || undefined
  }
  if (input.body !== input.template.body) {
    payload.body = input.body.trim() || undefined
  }
  return payload
}
