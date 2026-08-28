import { ref, watch, type Ref } from 'vue'

export interface DocumentSendContext {
  show: boolean
  docType: 'invoice' | 'quote' | 'receipt'
  docId: string
  customerEmail: string | null | undefined
  customerLocale: 'en' | 'zh' | null | undefined
}

export interface FrozenDocumentSendContext {
  context: DocumentSendContext
  version: number
}

function snapshot(context: DocumentSendContext): DocumentSendContext {
  return { ...context }
}

function sameContext(left: DocumentSendContext, right: DocumentSendContext): boolean {
  return left.show === right.show
    && left.docType === right.docType
    && left.docId === right.docId
    && left.customerEmail === right.customerEmail
    && left.customerLocale === right.customerLocale
}

/**
 * Own the mutable context of DocumentSendDialog.
 *
 * The dialog is reused by payment rows, so a late template/defaults response
 * must never overwrite a newer document's form.  A send freezes the endpoint
 * and form context; a prop change during that send is reloaded only after it
 * finishes, and cannot close the newer context.
 */
export function useDocumentSendContext(
  readContext: () => DocumentSendContext,
  reset: () => void,
  load: (context: DocumentSendContext, isCurrent: () => boolean) => Promise<void>,
): {
  sending: Ref<boolean>
  reload: () => Promise<void>
  beginSend: () => FrozenDocumentSendContext | null
  finishSend: (frozen: FrozenDocumentSendContext) => boolean
} {
  const sending = ref(false)
  let contextVersion = 0

  async function reload(): Promise<void> {
    const context = snapshot(readContext())
    if (!context.show || sending.value) return
    const version = ++contextVersion
    reset()
    await load(context, () => (
      !sending.value
      && version === contextVersion
      && sameContext(context, readContext())
    ))
  }

  watch(readContext, () => {
    contextVersion += 1
    const context = readContext()
    if (!context.show) {
      reset()
      return
    }
    // A sending request owns its frozen context.  Once it settles, finishSend
    // will load the latest requested context.
    if (!sending.value) void reload()
  }, { immediate: true })

  function beginSend(): FrozenDocumentSendContext | null {
    if (sending.value) return null
    sending.value = true
    return { context: snapshot(readContext()), version: contextVersion }
  }

  function finishSend(frozen: FrozenDocumentSendContext): boolean {
    sending.value = false
    const current = readContext()
    const unchanged = current.show
      && frozen.version === contextVersion
      && sameContext(frozen.context, current)
    if (!unchanged && current.show) void reload()
    return unchanged
  }

  return { sending, reload, beginSend, finishSend }
}
