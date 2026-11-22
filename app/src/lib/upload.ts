import type { PrepareReport, QueueFile } from './api'
import { formatSize } from './describe'

export type QueueState = 'failed' | 'uploading' | 'queued' | 'done' | 'submitted'
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
  /** On SFTP but not yet marked submitted. */
  uploaded: number
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

// What needs attention comes first.
export const STATE_VIEWS: Record<QueueState, { label: string; dot: string; rank: number }> = {
  failed: { label: 'Ошибка', dot: 'bg-danger', rank: 0 },
  uploading: { label: 'Загружается', dot: 'bg-accent', rank: 1 },
  queued: { label: 'В очереди', dot: 'bg-ink-muted', rank: 2 },
  done: { label: 'На SFTP', dot: 'bg-success', rank: 3 },
  submitted: { label: 'Отправлен', dot: 'bg-success', rank: 4 },
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
    if (file.status === 'submitted') return { ...base, state: 'submitted', progress: 1 }
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
    done: rows.filter((row) => row.state === 'done' || row.state === 'submitted').length,
    uploaded: rows.filter((row) => row.state === 'done').length,
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

/** "42% · 12 МБ из 30 МБ" (the unit is dropped from the first size). */
export function progressText(totals: QueueTotals): string {
  const percent = Math.round(fractionOf(totals) * 100)
  const sent = formatSize(Math.round(totals.sentBytes))
  return `${percent}% · ${sent.slice(0, -3)} из ${formatSize(totals.totalBytes)}`
}

/** What the «Загрузить» button will do: how many files go out and how many are skipped as already on SFTP. */
export function uploadHint(totals: QueueTotals): string {
  const waiting = totals.total - totals.done
  if (totals.total === 0) return 'Нет файлов для загрузки'
  if (waiting === 0) return 'Все файлы уже на SFTP: загружать нечего'
  return `Будет загружено: ${waiting}. Уже на SFTP, не загружаются повторно: ${totals.done}`
}

const SHOWN_PROBLEMS = 3

/** Why the files cannot go to SFTP after preparing it; empty when every copy is ready. */
export function prepareTrouble(report: PrepareReport): string {
  if (report.problems.length > 0) {
    const shown = report.problems.slice(0, SHOWN_PROBLEMS).join('; ')
    const rest = report.problems.length - SHOWN_PROBLEMS
    return `Не подготовлено, исправьте на странице «Описание»: ${shown}${rest > 0 ? ` (и ещё ${rest})` : ''}`
  }
  if (report.failed.length > 0) {
    return `Не удалось подготовить файлов: ${report.failed.length}. ${report.failed[0]}`
  }
  if (report.stopped) return 'Подготовка приостановлена'
  return ''
}
