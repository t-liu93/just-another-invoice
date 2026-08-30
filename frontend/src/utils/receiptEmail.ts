/** Pure receipt-email UI decisions shared by quote and invoice payment panels. */

export interface ReceiptCustomer {
  id: string
  email?: string | null
  locale?: 'en' | 'zh' | null
}

export interface ReceiptAuditLog {
  related_type: 'INVOICE' | 'QUOTE' | 'REFUND'
  related_id: string
}

export function persistedReceiptCustomer(
  sourceCustomerId: string | null | undefined,
  customers: readonly ReceiptCustomer[],
): ReceiptCustomer | null {
  return customers.find(customer => customer.id === sourceCustomerId) ?? null
}

export function receiptAuditTarget(
  currentInvoiceId: string,
  log: ReceiptAuditLog,
): 'refresh-invoice' | { quoteId: string } | null {
  if (log.related_type === 'INVOICE' && log.related_id === currentInvoiceId) {
    return 'refresh-invoice'
  }
  if (log.related_type === 'REFUND') return null
  return { quoteId: log.related_id }
}

export function openReceiptDialog(
  paymentId: string,
  _activePaymentId: string | null,
  sending: boolean,
): { paymentId: string; show: boolean } | null {
  // A receipt send owns the whole panel context.  Do not allow a different
  // payment to replace the dialog while its frozen request is in flight.
  if (sending) return null
  return { paymentId, show: true }
}
