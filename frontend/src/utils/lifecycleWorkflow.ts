import type { components } from '../api/schema'

type Action = components['schemas']['DocumentChainAvailableActionRead']
type Node = components['schemas']['DocumentChainNodeRead']
type AdvanceInputMode = components['schemas']['AdvanceInputMode']
type CreditLineInputMode = components['schemas']['CreditLineInputMode']

/** Backend projection is the only authority for command availability. */
export function availableAction(actions: Action[] | undefined, code: string, targetId?: string | null, targetType?: 'QUOTE' | 'INVOICE'): boolean {
  return actions?.some(action => action.code === code && action.available
    && (targetId === undefined || action.target_id === targetId)
    && (targetType === undefined || action.target_type === targetType)) ?? false
}

/** A chain can contain relation endpoints and cash rows; show each node once. */
export function uniqueTimelineNodes(nodes: Node[] | undefined): Node[] {
  const seen = new Set<string>()
  return (nodes ?? []).filter(node => {
    // Payments and documents are separate namespaces.  UUIDs normally make a
    // collision unlikely, but the API deliberately models a typed graph and
    // callers must not turn two different typed nodes into one row.
    const key = `${node.node_type}:${node.id}`
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
}

export function advanceIntent(mode: AdvanceInputMode, raw: string): {
  input_mode: AdvanceInputMode
  gross_amount?: string
  percentage?: string
} {
  return mode === 'GROSS_AMOUNT'
    ? { input_mode: mode, gross_amount: raw }
    : { input_mode: mode, percentage: raw }
}

export interface CreditIntentRow {
  source_basis_line_id: string
  input_mode: CreditLineInputMode
  raw: string
}

/** Keep the browser payload as the user's raw selected basis and input only. */
export function creditIntent(fullRemaining: boolean, rows: CreditIntentRow[]): {
  full_remaining: boolean
  lines?: Array<{ source_basis_line_id: string; input_mode: CreditLineInputMode; quantity?: string; gross_amount?: string }>
} {
  if (fullRemaining) return { full_remaining: true }
  return {
    full_remaining: false,
    lines: rows.filter(row => row.source_basis_line_id && row.raw.trim()).map(row => (
      row.input_mode === 'QUANTITY'
        ? { source_basis_line_id: row.source_basis_line_id, input_mode: row.input_mode, quantity: row.raw }
        : { source_basis_line_id: row.source_basis_line_id, input_mode: row.input_mode, gross_amount: row.raw }
    )),
  }
}

export function apiErrorCode(detail: unknown): string | null {
  if (!detail || typeof detail !== 'object') return null
  const body = detail as { detail?: unknown; code?: unknown }
  if (typeof body.code === 'string') return body.code
  if (body.detail && typeof body.detail === 'object' && typeof (body.detail as { code?: unknown }).code === 'string') {
    return (body.detail as { code: string }).code
  }
  return null
}
