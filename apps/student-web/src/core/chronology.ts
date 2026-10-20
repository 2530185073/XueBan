export type PreciseTimestamp = [number, string] | null

/** Preserve Python's microseconds, including equivalent RFC3339 time zones. */
export function preciseTimestamp(value: string | undefined): PreciseTimestamp {
  const milliseconds = Date.parse(value ?? '')
  if (!Number.isFinite(milliseconds)) return null
  const fraction = /\.(\d+)(?:Z|[+-]\d{2}:\d{2})$/i.exec(value ?? '')?.[1] ?? ''
  return [milliseconds, fraction.slice(3, 9).padEnd(6, '0')]
}
export const compareTimestamps = (a: PreciseTimestamp, b: PreciseTimestamp) => a && b ? a[0] - b[0] || a[1].localeCompare(b[1]) : a ? -1 : b ? 1 : 0
export const compareCreated = (a: { created_at?: string; id: string }, b: { created_at?: string; id: string }) => compareTimestamps(preciseTimestamp(a.created_at), preciseTimestamp(b.created_at)) || a.id.localeCompare(b.id)
