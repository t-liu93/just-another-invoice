/**
 * Format a Date as a local calendar YYYY-MM-DD string.
 * Do NOT use toISOString() — it converts to UTC first, which shifts the date
 * by 1–2 hours in CET/CEST timezones, causing dates to appear one day early
 * during the early hours of the morning.
 */
export function localDateStr(d: Date): string {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}
