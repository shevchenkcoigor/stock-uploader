import type { CsvEntry, CsvReport, CsvRow } from './api'
import { categoryName } from './categories'
import { viewOf } from './status'

export type ListField = 'name' | 'batch' | 'rows' | 'created' | 'updated' | 'status'
export type RowField = 'name' | 'title' | 'keywords' | 'category'

export const PORTAL_STEPS =
  'Дальше: загрузите картинки по SFTP (страница «Загрузка»), затем в портале Adobe Stock: Uploaded Files → New → Upload CSV; галочки отметьте по чеклисту. Загрузка CSV и отправка выполняются вручную.'
export const NOTHING_TO_ADD =
  'Нет описанных файлов без CSV: сначала опишите картинки (страница «Описание»), затем соберите CSV.'

// Same words and order as adobe_stock/gui/csv_page.py
export const ENTRY_VIEWS: Record<CsvEntry['state'], { label: string; dot: string; rank: number }> = {
  error: { label: 'Ошибка', dot: 'bg-danger', rank: 0 },
  built: { label: 'Собран', dot: 'bg-accent', rank: 1 },
  sent: { label: 'Отправлен', dot: 'bg-success', rank: 2 },
}

export function plural(count: number, one: string, few: string, many: string): string {
  if (count % 10 === 1 && count % 100 !== 11) return one
  if (count % 10 >= 2 && count % 10 <= 4 && !(count % 100 >= 12 && count % 100 <= 14)) return few
  return many
}

export function rowsText(entry: Pick<CsvEntry, 'rows' | 'badRows'>): string {
  const text = `${entry.rows} ${plural(entry.rows, 'строка', 'строки', 'строк')}`
  return entry.badRows ? `${text} · ${entry.badRows} ${plural(entry.badRows, 'с ошибкой', 'с ошибками', 'с ошибками')}` : text
}

const waitingList = (waiting: Array<[string, number]>) =>
  waiting.map(([status, count]) => `${viewOf(status).label}: ${count}`).join(', ')

/** Why the batch cannot be marked as sent: the files that are not on SFTP yet, with their statuses. */
export function waitingText(entry: Pick<CsvEntry, 'waiting' | 'batchId' | 'folder' | 'part'>): string {
  if (entry.waiting.length === 0) return ''
  return `Нельзя отметить отправленным: батч ${entry.batchId} (${entry.folder} · ${entry.part}) загружен не полностью, ещё ${waitingList(entry.waiting)}.`
}

export const refusedText = (batch: number, waiting: Array<[string, number]>) =>
  `Не отмечено: батч ${batch} загружен не полностью, ещё ${waitingList(waiting)}.`

const pad = (value: number) => String(value).padStart(2, '0')

/** A CSV's creation / update time for the list; a dash when the file is missing. */
export function dateText(seconds: number): string {
  if (!seconds) return '—'
  const at = new Date(seconds * 1000)
  return `${pad(at.getDate())}.${pad(at.getMonth() + 1)}.${at.getFullYear()} ${pad(at.getHours())}:${pad(at.getMinutes())}`
}

export function sortEntries(entries: CsvEntry[], field: ListField, descending: boolean): CsvEntry[] {
  const key = (entry: CsvEntry): number | string | [number, string] =>
    ({
      name: entry.name.toLowerCase(),
      batch: entry.batchId,
      rows: entry.rows,
      created: entry.created,
      updated: entry.updated,
      status: ENTRY_VIEWS[entry.state].rank * 1e6,
    })[field]
  const sign = descending ? -1 : 1
  return [...entries].sort((a, b) => {
    const [x, y] = [key(a), key(b)] as [number | string, number | string]
    const order = x < y ? -1 : x > y ? 1 : a.name.toLowerCase().localeCompare(b.name.toLowerCase())
    return order * sign
  })
}

export function sortRows(rows: CsvRow[], field: RowField, descending: boolean): CsvRow[] {
  const key = (row: CsvRow): [number, string] =>
    ({
      name: [0, row.filename.toLowerCase()] as [number, string],
      title: [row.title === '' ? 1 : 0, row.title.toLowerCase()] as [number, string], // empty ones go last
      keywords: [row.keywords.length === 0 ? 1 : 0, row.keywords.join(', ').toLowerCase()] as [number, string],
      category: [row.category ? 0 : 1, categoryName(row.category).toLowerCase()] as [number, string],
    })[field]
  const sign = descending ? -1 : 1
  return [...rows].sort((a, b) => {
    const [x, y] = [key(a), key(b)]
    const order = x[0] - y[0] || (x[1] < y[1] ? -1 : x[1] > y[1] ? 1 : 0)
    return order * sign
  })
}

/** The text under the table after a build. */
export function buildText(report: CsvReport): string {
  if (report.rows === 0) return 'В батче нет описанных и неотправленных файлов.'
  if (report.problems.length > 0) return `ПРОБЛЕМА CSV: ${report.problems.join('; ')}`
  return `CSV собран: ${report.rows} строк, проверен по правилам Adobe. «People and Property are fictional» — ${report.people} файлов с людьми. ${PORTAL_STEPS}`
}
