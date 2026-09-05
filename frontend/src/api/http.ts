/**
 * Lightweight fetch wrapper for the JAI backend API.
 *
 * - All requests go through the Vite dev proxy (``/api`` -> fixed backend port
 *   8000) or directly to the backend in production (single-container deployment).
 * - Throws ``ApiError`` on non-2xx responses with a best-effort human-readable
 *   message extracted from the FastAPI / Pydantic error shape.
 */

/** Error thrown when the backend returns a non-2xx response. */
export class ApiError extends Error {
  status: number
  detail: unknown

  constructor(status: number, detail: unknown, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

export function humanMessage(status: number, body: unknown): string {
  if (typeof body === 'string') return body
  if (body && typeof body === 'object') {
    // FastAPI/Pydantic validation error
    if ('detail' in body) {
      const d = (body as { detail: unknown }).detail
      if (Array.isArray(d)) {
        return d
          .map((e: unknown) => {
            if (typeof e === 'object' && e !== null && 'msg' in e) {
              return String((e as { msg: string }).msg)
            }
            return String(e)
          })
          .join('; ')
      }
      if (typeof d === 'object' && d !== null && 'reason' in d) {
        return String((d as { reason: string }).reason)
      }
      if (typeof d === 'object' && d !== null && 'message' in d) {
        const message = (d as { message: unknown }).message
        if (typeof message === 'string') return message
      }
      return String(d)
    }
    // { code, reason } shape
    if ('reason' in body) return String((body as { reason: string }).reason)
  }
  return `Request failed with status ${status}`
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/** Perform a JSON fetch and return the parsed response body. */
export async function http<T>(
  url: string,
  options: RequestInit = {},
): Promise<T> {
  const headers: Record<string, string> = {
    ...(options.headers as Record<string, string> | undefined),
  }
  if (
    options.body !== undefined &&
    !headers['Content-Type'] &&
    !(options.body instanceof FormData)
  ) {
    headers['Content-Type'] = 'application/json'
  }

  const res = await fetch(url, {
    ...options,
    headers,
    credentials: 'include',
  })

  if (res.status === 204) return null as T

  if (!res.ok) {
    let body: unknown
    try {
      body = await res.json()
    } catch {
      body = await res.text().catch(() => null)
    }
    throw new ApiError(res.status, body, humanMessage(res.status, body))
  }

  return res.json() as Promise<T>
}

/** Convenience helpers --------------------------------------------------- */

export function get<T>(url: string): Promise<T> {
  return http<T>(url)
}

export function post<T>(url: string, body: unknown): Promise<T> {
  return http<T>(url, { method: 'POST', body: JSON.stringify(body) })
}

export function put<T>(url: string, body: unknown): Promise<T> {
  return http<T>(url, { method: 'PUT', body: JSON.stringify(body) })
}

export function patch<T>(url: string, body: unknown): Promise<T> {
  return http<T>(url, { method: 'PATCH', body: JSON.stringify(body) })
}

export function del<T>(url: string): Promise<T> {
  return http<T>(url, { method: 'DELETE' })
}

/** POST with URL-encoded body (e.g. OAuth2 login). */
export function postForm<T>(url: string, data: Record<string, string>): Promise<T> {
  return http<T>(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams(data).toString(),
  })
}

/** PUT with multipart/form-data (e.g. file upload). */
export function uploadFile<T>(
  url: string,
  file: File,
  fieldName: string = 'file',
): Promise<T> {
  const formData = new FormData()
  formData.append(fieldName, file)
  // Do NOT set Content-Type — the browser sets it with the multipart boundary.
  return http<T>(url, {
    method: 'PUT',
    body: formData,
  })
}

/** POST multipart/form-data without manually setting the boundary. */
export function postFile<T>(
  url: string,
  file: File,
  fieldName: string = 'file',
): Promise<T> {
  const formData = new FormData()
  formData.append(fieldName, file)
  return http<T>(url, { method: 'POST', body: formData })
}

/**
 * Fetch a binary resource (e.g. PDF) and trigger a browser download.
 *
 * - Uses `credentials: 'include'` so the session cookie is sent.
 * - On non-2xx responses, reads the body as JSON/text and throws `ApiError`.
 * - The filename is extracted from the `Content-Disposition` response header
 *   when present; otherwise falls back to `fallbackFilename`.
 */
/**
 * Extract the filename from a ``Content-Disposition`` header, e.g.:
 *   attachment; filename="INV-2025-001.pdf"
 *   attachment; filename*=UTF-8''INV-2025-001.pdf
 * Falls back to ``fallbackFilename`` when no filename is present.
 */
function filenameFromContentDisposition(
  cd: string | null,
  fallbackFilename: string,
): string {
  if (cd) {
    const utf8Match = cd.match(/filename\*=UTF-8''([^;]+)/i)
    if (utf8Match) {
      return decodeURIComponent(utf8Match[1])
    }
    const plainMatch = cd.match(/filename="?([^";]+)"?/i)
    if (plainMatch) {
      return plainMatch[1].trim()
    }
  }
  return fallbackFilename
}

/**
 * Trigger a browser save dialog for an already-fetched blob.
 */
export function saveBlob(blob: Blob, filename: string): void {
  const objectUrl = URL.createObjectURL(blob)
  try {
    const a = document.createElement('a')
    a.href = objectUrl
    a.download = filename
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
  } finally {
    URL.revokeObjectURL(objectUrl)
  }
}

/**
 * Fetch a binary resource (e.g. PDF) and trigger a browser download.
 *
 * - Uses `credentials: 'include'` so the session cookie is sent.
 * - On non-2xx responses, reads the body as JSON/text and throws `ApiError`.
 * - The filename is extracted from the `Content-Disposition` response header
 *   when present; otherwise falls back to `fallbackFilename`.
 */
export async function downloadBlob(
  url: string,
  fallbackFilename: string = 'download',
): Promise<void> {
  const { blob, filename } = await fetchBlob(url, fallbackFilename)
  saveBlob(blob, filename)
}

/**
 * Fetch a binary resource (e.g. PDF) and return its blob plus the filename
 * advertised by the backend in `Content-Disposition`.
 *
 * Unlike `downloadBlob`, this does NOT trigger a save dialog — the caller
 * decides what to do (e.g. render it inline via `URL.createObjectURL`).  The
 * caller owns the returned blob's lifecycle (and any object URL it creates).
 */
export async function fetchBlob(
  url: string,
  fallbackFilename: string = 'download',
): Promise<{ blob: Blob; filename: string }> {
  const res = await fetch(url, { credentials: 'include' })

  if (!res.ok) {
    let body: unknown
    try {
      body = await res.json()
    } catch {
      body = await res.text().catch(() => null)
    }
    throw new ApiError(res.status, body, humanMessage(res.status, body))
  }

  const blob = await res.blob()
  const filename = filenameFromContentDisposition(
    res.headers.get('Content-Disposition'),
    fallbackFilename,
  )
  return { blob, filename }
}
