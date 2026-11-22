import type { FileRow } from './api'
import { categoryName } from './categories'
import { viewOf } from './status'

export const FILTER_ALL = 'all'
export const EDITABLE_STATUSES = ['needs_metadata', 'pending', 'review', 'uploaded'] as const
// Files that may be captioned (again): new, described and flagged ones. Uploaded and submitted never.
export const RECAPTIONABLE: readonly string[] = ['needs_metadata', 'pending', 'review']
const BYTES_IN_MB = 1024 * 1024

export type SortField = 'name' | 'title' | 'keywords' | 'category' | 'resolution' | 'size' | 'status'
export type Dimensions = Readonly<Record<string, readonly [number, number]>>

/** 'a, b,, a' -> ['a', 'b']: trimmed, no empty or repeated tags, order kept. */
export function parseKeywords(text: string): string[] {
  return [...new Set(text.replace(/\n/g, ',').split(',').map((part) => part.trim()).filter(Boolean))]
}

export const formatSize = (bytes: number): string => `${(bytes / BYTES_IN_MB).toFixed(1).replace('.', ',')} МБ`

export const resolutionText = (size: readonly [number, number] | undefined): string =>
  size && size[0] && size[1] ? `${size[0]} × ${size[1]}` : ''

export const filterRows = (rows: FileRow[], wanted: string): FileRow[] =>
  wanted === FILTER_ALL ? rows : rows.filter((row) => row.status === wanted)

type Key = Array<string | number | boolean>

function sortKey(field: SortField, row: FileRow, sizes: Dimensions): Key {
  const [width, height] = sizes[row.path] ?? [0, 0]
  switch (field) {
    case 'name':
      return [row.filename.toLowerCase()]
    case 'title':
      return [row.title === '', row.title.toLowerCase()] // empty ones go last
    case 'keywords':
      return [row.keywords.length === 0, row.keywords.join(', ').toLowerCase()]
    case 'category':
      return [!row.category, categoryName(row.category).toLowerCase()]
    case 'resolution':
      return [width * height, width] // more pixels sort later; unreadable files (0) come first
    case 'size':
      return [row.size]
    default:
      return [viewOf(row.status).rank, row.filename.toLowerCase()]
  }
}

function compareKeys(left: Key, right: Key): number {
  for (let index = 0; index < left.length; index += 1) {
    const [a, b] = [left[index], right[index]]
    if (a === b) continue
    if (typeof a === 'string') return a.localeCompare(String(b))
    return Number(a) - Number(b)
  }
  return 0
}

/** The rows ordered by a column; rows with equal keys keep their order (the time they were added). */
export function sortRows(rows: FileRow[], field: SortField, descending: boolean, sizes: Dimensions = {}): FileRow[] {
  const direction = descending ? -1 : 1
  return [...rows].sort((a, b) => direction * compareKeys(sortKey(field, a, sizes), sortKey(field, b, sizes)))
}

/** What a status change did; a file without a description cannot leave "new", and that is said. */
export function movedText(moved: number, names: string[], status: string, rows: FileRow[]): string {
  const text = `Статус сменён: ${moved} из ${names.length}`
  if (status === 'needs_metadata') return text
  const wanted = new Set(names)
  const withoutText = rows.filter((row) => wanted.has(row.filename) && !(row.title && row.keywords.length)).length
  return withoutText ? `${text} (без заголовка или ключевых слов: ${withoutText})` : text
}

/** "Sonnet 5.5" from "claude-sonnet-5-5". */
export function modelTitle(id: string): string {
  const parts = id.replace(/^claude-/, '').split('-')
  const isNumber = (part: string) => /^\d+$/.test(part)
  const words = parts.filter((part) => !isNumber(part)).map((part) => part.charAt(0).toUpperCase() + part.slice(1))
  return [...words, parts.filter(isNumber).join('.')].join(' ').trim()
}

/** The result text shown under the table after a caption run. */
export function captionSummary(report: {
  total: number
  captioned: number
  flagged: string[]
  newBatches: number
  stopped: boolean
}): string {
  if (report.total === 0) return 'Нечего подписывать: среди выбранных нет файлов, которые можно подписать'
  const parts = [
    `Подписано: ${report.captioned} из ${report.total}`,
    `на проверке: ${report.flagged.length}`,
    `новых батчей: ${report.newBatches}`,
  ]
  return parts.join(' · ') + (report.stopped ? ' · отменено' : '')
}
