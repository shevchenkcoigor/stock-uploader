import type { QueueFile } from './api'
import { formatSize } from './describe'
import { viewOf } from './status'

export type QueueState = 'failed' | 'uploading' | 'queued' | 'done'
export type UploadSort = 'name' | 'size' | 'progress' | 'status'

export interface UploadRow {
  filename: string
  path: string
  size: number
  state: QueueState
  /** 0..1 of this file. */
  progress: number
}

export interface QueueTotals {
  total: number
  done: number
  failed: number
  sentBytes: number
  totalBytes: number
}

/** Which file is on the wire and how much of it is sent (from the progress events). */
export interface Current {
  file: string
  sent: number
  size: number
}

// Same words and order as adobe_stock/gui/upload_page.py: what needs attention comes first.
export const STATE_VIEWS: Record<QueueState, { label: string; dot: string; rank: number }> = {
  failed: { label: 'Ошибка', dot: 'bg-danger', rank: 0 },
  uploading: { label: 'Загружается', dot: 'bg-accent', rank: 1 },
  queued: { label: 'В очереди', dot: 'bg-ink-muted', rank: 2 },
  done: { label: 'На SFTP', dot: 'bg-success', rank: 3 },
}

const DONE_STATUSES = ['uploaded', 'submitted']

/**
 * Uploaded files are done, pending ones with an error failed. While a run is on, the file on the wire is
 * "uploading" (before the first progress event: the first of the rest).
 */
export function rowsFromQueue(files: QueueFile[], running: boolean, current: Current | null): UploadRow[] {
  const target = running ? (current?.file ?? files.find((f) => !DONE_STATUSES.includes(f.status))?.filename) : null
  return files.map((file) => {
    const base = { filename: file.filename, path: file.path, size: file.size }
    if (DONE_STATUSES.includes(file.status)) return { ...base, state: 'done', progress: 1 }
    if (file.filename === target) {
      const fraction = current?.file === file.filename && current.size > 0 ? current.sent / current.size : 0
      return { ...base, state: 'uploading', progress: Math.min(1, fraction) }
    }
    return { ...base, state: file.error ? 'failed' : 'queued', progress: 0 }
  })
}

export function totalsOf(rows: UploadRow[]): QueueTotals {
  return {
    total: rows.length,
    done: rows.filter((row) => row.state === 'done').length,
    failed: rows.filter((row) => row.state === 'failed').length,
    sentBytes: rows.reduce((sum, row) => sum + row.size * row.progress, 0),
    totalBytes: rows.reduce((sum, row) => sum + row.size, 0),
  }
}

export const fractionOf = (totals: QueueTotals) => (totals.totalBytes ? totals.sentBytes / totals.totalBytes : 0)

export function sortedRows(rows: UploadRow[], field: UploadSort, descending: boolean): UploadRow[] {
  const key = (row: UploadRow): number | string =>
    ({
      name: row.filename.toLowerCase(),
      size: row.size,
      progress: row.progress,
      status: STATE_VIEWS[row.state].rank,
    })[field]
  const sign = descending ? -1 : 1
  return [...rows].sort((a, b) => {
    const [x, y] = [key(a), key(b)]
    const order = x < y ? -1 : x > y ? 1 : 0
    return order !== 0 ? order * sign : a.filename.toLowerCase().localeCompare(b.filename.toLowerCase())
  })
}

export function errorsWord(count: number): string {
  if (count % 10 === 1 && count % 100 !== 11) return 'ошибка'
  if (count % 10 >= 2 && count % 10 <= 4 && !(count % 100 >= 12 && count % 100 <= 14)) return 'ошибки'
  return 'ошибок'
}

export function summaryText(totals: QueueTotals): string {
  const text = `${totals.done} из ${totals.total} загружено`
  return totals.failed ? `${text} · ${totals.failed} ${errorsWord(totals.failed)}` : text
}

/** "42% · 12 МБ из 30 МБ" (the unit is dropped from the first size, as in the PySide app). */
export function progressText(totals: QueueTotals): string {
  const percent = Math.round(fractionOf(totals) * 100)
  const sent = formatSize(Math.round(totals.sentBytes))
  return `${percent}% · ${sent.slice(0, -3)} из ${formatSize(totals.totalBytes)}`
}

/** A batch's status for people: one word when all its files share a status, else the counts. */
export function batchStatusText(counts: ReadonlyArray<{ status: string; n: number }>): string {
  if (counts.length === 0) return '—'
  const ordered = [...counts].sort((a, b) => viewOf(a.status).rank - viewOf(b.status).rank)
  if (ordered.length === 1) return viewOf(ordered[0].status).label
  return ordered.map(({ status, n }) => `${viewOf(status).label}: ${n}`).join(', ')
}
