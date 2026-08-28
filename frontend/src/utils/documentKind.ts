import type { components } from '../api/schema'

type InvoiceDocumentKind = components['schemas']['InvoiceDocumentKind']

/**
 * Return the i18n key for an invoice kind, preserving the legacy Standard
 * display when an older compatible API response omits the optional field.
 */
export function invoiceDocumentKindLabelKey(
  kind: InvoiceDocumentKind | null | undefined,
): 'invoices.documentKindSTANDARD' | 'invoices.documentKindADVANCE' | 'invoices.documentKindFINAL' | 'invoices.documentKindCREDIT_NOTE' {
  const normalized = kind ?? 'STANDARD'
  return `invoices.documentKind${normalized}`
}
