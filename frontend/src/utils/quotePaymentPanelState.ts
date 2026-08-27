/**
 * Small, side-effect-free guards for QuotePaymentPanel's reused route context.
 * Keeping these checks separate makes the A -> B route transition behaviour
 * directly testable without a component-test runner.
 */
export function isCurrentQuotePaymentContext(
  expectedQuoteId: string,
  currentQuoteId: string,
  expectedVersion: number,
  currentVersion: number,
): boolean {
  return expectedQuoteId === currentQuoteId && expectedVersion === currentVersion
}

export function hasCurrentQuotePayment(
  paymentId: string,
  payments: readonly { id: string }[],
): boolean {
  return payments.some(payment => payment.id === paymentId)
}

/**
 * One panel must apply at most one payment mutation at a time. Keeping this
 * guard pure makes the record/edit/delete interlock testable without mounting
 * either Naive UI panel.
 */
export function isPaymentMutationBusy(
  recordSaving: boolean,
  editSaving: boolean,
  deleteSaving: string | null,
): boolean {
  return recordSaving || editSaving || deleteSaving !== null
}
