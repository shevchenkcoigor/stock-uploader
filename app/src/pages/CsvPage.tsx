import { ask } from '@tauri-apps/plugin-dialog'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Pager } from '../components/Pager'
import { csvApi, filesApi, type CsvEntry, type CsvOverview, type CsvRow } from '../lib/api'
import { categoryName } from '../lib/categories'
import {
  ENTRY_VIEWS,
  NOTHING_TO_ADD,
  buildText,
  dateText,
  refusedText,
  rowsText,
  sortEntries,
  sortRows,
  waitingText,
  type ListField,
  type RowField,
} from '../lib/csv'
import { DATA_CHANGED_EVENT } from '../lib/events'
import { batchStatusText } from '../lib/upload'
import type { PageProps } from './pages'

const ROW_PAGE_SIZE = 20
const LIST_COLUMNS: ReadonlyArray<{ field: ListField | null; title: string; width?: string }> = [
  { field: 'name', title: 'Имя' },
  { field: 'batch', title: 'Батч', width: '110px' },
  { field: 'rows', title: 'Строк', width: '64px' },
  { field: 'created', title: 'Создан', width: '120px' },
  { field: 'updated', title: 'Обновлён', width: '120px' },
  { field: 'status', title: 'Статус', width: '110px' },
  { field: null, title: '', width: '120px' },
]
const ROW_COLUMNS: ReadonlyArray<{ field: RowField | null; title: string; width?: string }> = [
  { field: 'name', title: 'Filename', width: '170px' },
  { field: 'title', title: 'Title' },
  { field: 'keywords', title: 'Keywords' },
  { field: 'category', title: 'Category', width: '140px' },
  { field: null, title: 'Releases', width: '90px' },
]
const secondary =
  'h-8 rounded-md border border-line-strong bg-surface px-3 text-sm outline-none hover:bg-sunken focus-visible:ring-2 focus-visible:ring-accent disabled:opacity-50'
const dash = <span className="italic text-ink-muted">—</span>

function Header<T extends string>({
  columns,
  sort,
  onSort,
}: {
  columns: ReadonlyArray<{ field: T | null; title: string }>
  sort: { field: T; descending: boolean }
  onSort: (field: T) => void
}) {
  return (
    <thead className="sticky top-0 z-10 bg-surface text-left text-xs text-ink-muted">
      <tr>
        {columns.map(({ field, title }) => (
          <th
            key={title || 'action'}
            className="border-b border-line px-2 py-2 font-medium"
            aria-sort={field && sort.field === field ? (sort.descending ? 'descending' : 'ascending') : 'none'}
          >
            {field ? (
              <button type="button" onClick={() => onSort(field)} className="outline-none hover:text-ink focus-visible:ring-2 focus-visible:ring-accent">
                {title}
                {sort.field === field ? (sort.descending ? ' ▼' : ' ▲') : ''}
              </button>
            ) : (
              title
            )}
          </th>
        ))}
      </tr>
    </thead>
  )
}

export function CsvPage({ active }: PageProps) {
  const [overview, setOverview] = useState<CsvOverview>({ entries: [], offered: [], counts: [] })
  const [batch, setBatch] = useState<number | null>(null)
  const [offeredBatch, setOfferedBatch] = useState<number | null>(null)
  const [rows, setRows] = useState<CsvRow[]>([])
  const [busy, setBusy] = useState(false)
  const [listSort, setListSort] = useState<{ field: ListField; descending: boolean }>({ field: 'updated', descending: true })
  const [rowSort, setRowSort] = useState<{ field: RowField; descending: boolean }>({ field: 'name', descending: false })
  const [page, setPage] = useState(0)
  const [notice, setNotice] = useState<{ text: string; error: boolean } | null>(null)

  const say = useCallback((text: string, error = false) => setNotice({ text, error }), [])

  const load = useCallback(() => {
    csvApi
      .overview()
      .then((fresh) => {
        setOverview(fresh)
        setBatch((chosen) => (chosen !== null && fresh.entries.some((e) => e.batchId === chosen) ? chosen : null))
        setOfferedBatch((chosen) =>
          chosen !== null && fresh.offered.some((o) => o.batchId === chosen) ? chosen : (fresh.offered.at(-1)?.batchId ?? null),
        )
      })
      .catch((reason) => say(String(reason), true))
  }, [say])

  useEffect(() => {
    load()
    window.addEventListener(DATA_CHANGED_EVENT, load)
    return () => window.removeEventListener(DATA_CHANGED_EVENT, load)
  }, [load])
  useEffect(() => {
    if (active && !busy) load()
  }, [active, busy, load])

  const entries = useMemo(() => sortEntries(overview.entries, listSort.field, listSort.descending), [overview.entries, listSort])
  const chosenId = batch ?? entries[0]?.batchId ?? null // nothing chosen yet: the first of the list
  const chosen: CsvEntry | null = entries.find((entry) => entry.batchId === chosenId) ?? null

  useEffect(() => {
    if (chosenId === null) return
    let current = true
    csvApi
      .rows(chosenId)
      .then((fresh) => current && setRows(fresh))
      .catch((reason) => say(String(reason), true))
    return () => {
      current = false
    }
  }, [chosenId, overview, say])

  // Arrow keys move the chosen CSV, as on the description page.
  useEffect(() => {
    if (!active) return
    const onKey = (event: KeyboardEvent) => {
      const typing = (event.target as HTMLElement | null)?.closest('input, select, textarea')
      const delta = { ArrowDown: 1, ArrowUp: -1 }[event.key]
      if (!delta || typing || event.metaKey || event.ctrlKey || event.altKey || event.shiftKey || entries.length === 0) return
      event.preventDefault()
      const at = entries.findIndex((entry) => entry.batchId === chosenId)
      setBatch(entries[Math.max(0, Math.min(entries.length - 1, at + delta))].batchId)
      setPage(0)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [active, entries, chosenId])

  const orderedRows = useMemo(
    () => (chosenId === null ? [] : sortRows(rows, rowSort.field, rowSort.descending)),
    [rows, rowSort, chosenId],
  )
  const shownPage = Math.min(page, Math.max(0, Math.ceil(orderedRows.length / ROW_PAGE_SIZE) - 1))
  const pageRows = orderedRows.slice(shownPage * ROW_PAGE_SIZE, (shownPage + 1) * ROW_PAGE_SIZE)

  const finish = () => {
    setBusy(false)
    window.dispatchEvent(new Event(DATA_CHANGED_EVENT))
  }

  const build = async (target: number) => {
    setBusy(true)
    try {
      const report = await csvApi.build(target)
      setBatch(report.batch)
      say(buildText(report), report.problems.length > 0)
    } catch (reason) {
      say(`ОШИБКА: ${reason}`, true)
    } finally {
      finish()
    }
  }

  const markSubmitted = async () => {
    if (chosenId === null) return
    const agreed = await ask(
      `Вы уже загрузили CSV батча ${chosenId} в портале и отправили файлы на проверку?\nБатч будет помечен как отправленный.`,
      { title: 'Отметить отправленным', kind: 'warning' },
    )
    if (!agreed) return
    setBusy(true)
    try {
      const report = await csvApi.markSubmitted(chosenId)
      if (report.waiting.length > 0) say(refusedText(chosenId, report.waiting), true)
      else say(`Отмечено отправленными: ${report.count}. ${report.warnings.map((w) => `ВНИМАНИЕ ${w}.`).join(' ')}`.trim())
    } catch (reason) {
      say(`ОШИБКА: ${reason}`, true)
    } finally {
      finish()
    }
  }

  const sortList = (field: ListField) => setListSort((now) => ({ field, descending: now.field === field && !now.descending }))
  const sortRowsBy = (field: RowField) => {
    setRowSort((now) => ({ field, descending: now.field === field && !now.descending }))
    setPage(0)
  }
  const statusOf = (id: number) => batchStatusText(overview.counts.filter((item) => item.batchId === id))
  const canSend = chosen !== null && chosen.state === 'built' && chosen.waiting.length === 0
  const hint = chosen !== null && chosen.state !== 'sent' ? waitingText(chosen) : ''
  const open = (path: string) => void filesApi.open(path).catch(() => say(`Файл не найден на диске: ${path}. Нажмите «Пересобрать».`, true))

  return (
    <div className="flex h-full flex-col gap-4 p-6">
      <div className="flex min-h-0 flex-1 gap-4">
        <section className="flex min-w-[560px] flex-[1.3] flex-col gap-3 rounded-lg border border-line bg-surface p-4">
          <div className="flex items-center gap-2">
            <h2 className="text-base font-semibold">Файлы</h2>
            <span className="flex-1 text-sm text-ink-muted">{entries.length}</span>
            {overview.offered.length > 0 && (
              <>
                <select
                  value={offeredBatch ?? ''}
                  aria-label="Батч для нового CSV"
                  disabled={busy}
                  onChange={(event) => setOfferedBatch(Number(event.target.value))}
                  className="h-8 max-w-72 rounded-md border border-line-strong bg-surface px-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:opacity-50"
                >
                  {overview.offered.map((item) => (
                    <option key={item.batchId} value={item.batchId}>
                      {`Батч ${item.batchId}: ${item.folder}, часть ${item.part} — ${item.n} файлов, статус: ${statusOf(item.batchId)}`}
                    </option>
                  ))}
                </select>
                <button type="button" className={secondary} disabled={busy || offeredBatch === null} onClick={() => offeredBatch !== null && void build(offeredBatch)}>
                  + CSV
                </button>
              </>
            )}
          </div>
          {overview.offered.length === 0 && <p className="text-sm text-ink-muted">{NOTHING_TO_ADD}</p>}

          <div className="min-h-0 flex-1 overflow-auto">
            <table className="w-full table-fixed border-collapse text-sm">
              <colgroup>
                {LIST_COLUMNS.map(({ title, width }) => (
                  <col key={title || 'action'} style={width ? { width } : undefined} />
                ))}
              </colgroup>
              <Header columns={LIST_COLUMNS} sort={listSort} onSort={sortList} />
              <tbody>
                {entries.map((entry) => {
                  const view = ENTRY_VIEWS[entry.state]
                  return (
                    <tr
                      key={entry.batchId}
                      aria-selected={entry.batchId === chosenId}
                      onClick={() => {
                        setBatch(entry.batchId)
                        setPage(0)
                      }}
                      className={`h-10 cursor-default border-b border-line ${entry.batchId === chosenId ? 'bg-accent-soft' : 'hover:bg-sunken'}`}
                    >
                      <td className="px-2">
                        <span className="block truncate font-mono text-xs" title={`${entry.path}\nДвойной клик: открыть файл`} onDoubleClick={() => open(entry.path)}>
                          {entry.name}
                        </span>
                      </td>
                      <td className="px-2">
                        <span
                          className="block truncate"
                          title={`Батч ${entry.batchId}: ${entry.folder}, часть ${entry.part}\nСтатус батча: ${statusOf(entry.batchId)}`}
                        >
                          {entry.folder} · {entry.part}
                        </span>
                      </td>
                      <td className="px-2 text-ink-muted">{entry.rows}</td>
                      <td className="px-2 text-ink-muted">{dateText(entry.created)}</td>
                      <td className="px-2 text-ink-muted">{dateText(entry.updated)}</td>
                      <td className="px-2">
                        <span className="inline-flex items-center gap-2">
                          <span aria-hidden className={`size-2.5 rounded-full ${view.dot}`} />
                          {view.label}
                        </span>
                      </td>
                      <td className="px-2" onClick={(event) => event.stopPropagation()}>
                        {entry.state !== 'sent' && (
                          <button type="button" className={secondary} disabled={busy} title={`Собрать ${entry.name} заново из базы`} onClick={() => void build(entry.batchId)}>
                            Пересобрать
                          </button>
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </section>

        <section className="flex min-w-0 flex-1 flex-col gap-3 rounded-lg border border-line bg-surface p-4">
          <div className="flex items-start gap-2">
            <div className="min-w-0 flex-1">
              <h2 className="truncate text-base font-semibold">{chosen ? chosen.name : 'Нет собранных CSV'}</h2>
              <p className="text-sm text-ink-muted">{chosen ? rowsText(chosen) : 'Загрузите файлы по SFTP и нажмите «+ CSV»'}</p>
              {hint && <p className="text-sm text-warning">{hint}</p>}
            </div>
            <button type="button" className={secondary} disabled={busy || !canSend} onClick={() => void markSubmitted()}>
              Отметить отправленным
            </button>
          </div>

          <div className="min-h-0 flex-1 overflow-auto">
            <table className="w-full table-fixed border-collapse text-sm">
              <colgroup>
                {ROW_COLUMNS.map(({ title, width }) => (
                  <col key={title} style={width ? { width } : undefined} />
                ))}
              </colgroup>
              <Header columns={ROW_COLUMNS} sort={rowSort} onSort={sortRowsBy} />
              <tbody>
                {pageRows.map((row) => (
                  <tr key={row.filename} className="h-10 border-b border-line">
                    <td className="px-2">
                      <span className="block truncate font-mono text-xs" title={row.path} onDoubleClick={() => open(row.path)}>
                        {row.filename}
                      </span>
                    </td>
                    <td className="px-2">
                      <span className="block truncate" title={row.title}>
                        {row.title || <span className="italic text-ink-muted">Нет заголовка</span>}
                      </span>
                    </td>
                    <td className="px-2">
                      <span className="block truncate" title={row.keywords.join(', ')}>
                        {row.keywords.length > 0 ? row.keywords.join(', ') : <span className="italic text-ink-muted">Нет ключевых слов</span>}
                      </span>
                    </td>
                    <td className="px-2">{row.category ? categoryName(row.category) : <span className="italic text-ink-muted">Нет категории</span>}</td>
                    <td className="px-2">{dash}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pager total={orderedRows.length} pageSize={ROW_PAGE_SIZE} page={shownPage} onPage={setPage} />
        </section>
      </div>
      <p aria-live="polite" className={`min-h-4 text-xs ${notice?.error ? 'text-danger' : 'text-ink-muted'}`}>
        {notice?.text}
      </p>
    </div>
  )
}
