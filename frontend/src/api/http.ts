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

function humanMessage(status: number, body: unknown): string {
  if (typeof body === 'string') return body
  if (body && typeof body === 'object') {
    // FastAPI/Pydantic validation error
    if ('detail' in body) {
      const d = (body as { detail: unknown }).detail
      if (Array.isArray(d)) {
        return d.map((e: unknown) => String(e)).join('; ')
      }
      if (typeof d === 'object' && d !== null && 'reason' in d) {
        return String((d as { reason: string }).reason)
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
  if (options.body !== undefined && !headers['Content-Type']) {
    headers['Content-Type'] = 'application/json'
  }

  const res = await fetch(url, {
    ...options,
    headers,
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
