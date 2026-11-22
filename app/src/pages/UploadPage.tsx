import { listen } from '@tauri-apps/api/event'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Pager } from '../components/Pager'
import { Thumb } from '../components/Thumb'
import { filesApi, uploadApi, type PendingBatch, type QueueFile, type UploadOverview, type UploadProgress } from '../lib/api'
import { formatSize } from '../lib/describe'
import { DATA_CHANGED_EVENT } from '../lib/events'
import {
  STATE_VIEWS,
  batchStatusText,
  fractionOf,
  progressText,
  rowsFromQueue,
  sortedRows,
  summaryText,
  totalsOf,
  type Current,
  type UploadSort,
} from '../lib/upload'
import type { PageProps } from './pages'

const PAGE_SIZE = 20
const THUMB_SIDE = 40
const ROW_PADDING = 12
const PERCENT = 100
const KEYCHAIN_NOTICE =
  'Подключаюсь к SFTP. Сейчас macOS может попросить пароль от Mac: так приложение получает сохранённый SFTP-пароль. Выберите «Всегда разрешать».'
const COLUMNS: ReadonlyArray<{ field: UploadSort; title: string; width?: string }> = [
  { field: 'name', title: 'Файл' },
  { field: 'size', title: 'Размер', width: '100px' },
  { field: 'progress', title: 'Прогресс', width: '240px' },
  { field: 'status', title: 'Статус', width: '150px' },
]
const secondary =
  'h-8 rounded-md border border-line-strong bg-surface px-3 text-sm outline-none hover:bg-sunken focus-visible:ring-2 focus-visible:ring-accent disabled:opacity-50'

function batchLabel(batch: PendingBatch, overview: UploadOverview): string {
  const counts = overview.counts.filter((item) => item.batchId === batch.batchId)
  return `Батч ${batch.batchId}: ${batch.folder}, часть ${batch.part} — ${batch.n} файлов, статус: ${batchStatusText(counts)}`
}

function Bar({ fraction, label }: { fraction: number; label: string }) {
  const percent = Math.round(fraction * PERCENT)
  return (
    <div className="h-2 overflow-hidden rounded-full bg-sunken" role="progressbar" aria-label={label} aria-valuenow={percent}>
      <div className="h-full bg-accent" style={{ width: `${percent}%` }} />
    </div>
  )
}

export function UploadPage({ active, navigate }: PageProps) {
  const [overview, setOverview] = useState<UploadOverview>({ batches: [], counts: [] })
  const [batch, setBatch] = useState<number | null>(null)
  const [files, setFiles] = useState<QueueFile[]>([])
  const [running, setRunning] = useState(false)
  const [current, setCurrent] = useState<Current | null>(null)
  const [doneCount, setDoneCount] = useState(0)
  const [sort, setSort] = useState<{ field: UploadSort; descending: boolean }>({ field: 'status', descending: false })
  const [page, setPage] = useState(0)
  const [notice, setNotice] = useState<{ text: string; error: boolean } | null>(null)
  const [finished, setFinished] = useState(false)

  const say = useCallback((text: string, error = false) => setNotice({ text, error }), [])

  const loadOverview = useCallback(() => {
    uploadApi
      .overview()
      .then((fresh) => {
        setOverview(fresh)
        // the last pending batch by default; a batch that is gone is replaced
        setBatch((chosen) =>
          chosen !== null && fresh.batches.some((item) => item.batchId === chosen)
            ? chosen
            : (fresh.batches.at(-1)?.batchId ?? null),
        )
      })
      .catch((reason) => say(String(reason), true))
  }, [say])

  const loadQueue = useCallback(() => {
    if (batch === null) return
    uploadApi
      .queue(batch)
      .then(setFiles)
      .catch((reason) => say(String(reason), true))
  }, [batch, say])

  useEffect(() => {
    loadOverview()
    window.addEventListener(DATA_CHANGED_EVENT, loadOverview)
    return () => window.removeEventListener(DATA_CHANGED_EVENT, loadOverview)
  }, [loadOverview])
  useEffect(() => {
    if (active && !running) loadOverview()
  }, [active, running, loadOverview])
  // the queue follows the chosen batch, and every finished file of a run
  useEffect(loadQueue, [loadQueue, doneCount])

  useEffect(() => {
    const unlisten = listen<UploadProgress>('upload-progress', ({ payload }) => {
      setCurrent({ file: payload.file, sent: payload.sent, size: payload.size })
      setDoneCount(payload.done)
      say(payload.message, payload.message.startsWith('FAILED'))
    })
    return () => void unlisten.then((stop) => stop())
  }, [say])

  const run = async () => {
    if (batch === null || running) return
    setRunning(true)
    setFinished(false)
    setCurrent(null)
    say(KEYCHAIN_NOTICE)
    try {
      const report = await uploadApi.start(batch)
      if (report.error) {
        say(`Не удалось загрузить: ${report.error}. Файлы остались в очереди: проверьте SFTP в «Настройках».`, true)
      } else {
        const failed = report.failed.length
        const parts = [`Загружено: ${report.uploaded} из ${report.total}, ошибок: ${failed}`]
        if (report.stopped) parts.push('приостановлено')
        say(parts.join(' · '), failed > 0)
        setFinished(report.uploaded > 0 && failed === 0 && !report.stopped)
      }
    } catch (reason) {
      say(`ОШИБКА: ${reason}`, true)
    } finally {
      setRunning(false)
      setCurrent(null)
      window.dispatchEvent(new Event(DATA_CHANGED_EVENT))
    }
  }

  // no batch, no queue
  const rows = useMemo(() => rowsFromQueue(batch === null ? [] : files, running, current), [batch, files, running, current])
  const totals = totalsOf(rows)
  const ordered = useMemo(() => sortedRows(rows, sort.field, sort.descending), [rows, sort])
  const shownPage = Math.min(page, Math.max(0, Math.ceil(ordered.length / PAGE_SIZE) - 1))
  const visible = ordered.slice(shownPage * PAGE_SIZE, (shownPage + 1) * PAGE_SIZE)
  const waiting = totals.done < totals.total

  const sortBy = (field: UploadSort) => {
    setSort((now) => ({ field, descending: now.field === field && !now.descending }))
    setPage(0)
  }
  const open = (path: string) => void filesApi.open(path).catch(() => undefined)

  return (
    <div className="flex h-full flex-col gap-4 p-6">
      <label className="flex items-center gap-3 text-sm">
        <span className="text-ink-muted">Батч</span>
        <select
          value={batch ?? ''}
          disabled={running || overview.batches.length === 0}
          onChange={(event) => {
            setBatch(Number(event.target.value))
            setPage(0)
            setFinished(false)
          }}
          className="h-9 min-w-0 flex-1 rounded-md border border-line-strong bg-surface px-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:opacity-50"
        >
          {overview.batches.length === 0 && <option value="">Нет файлов, ожидающих загрузки</option>}
          {overview.batches.map((item) => (
            <option key={item.batchId} value={item.batchId}>
              {batchLabel(item, overview)}
            </option>
          ))}
        </select>
      </label>

      <section className="flex min-h-0 flex-1 flex-col gap-3 rounded-lg border border-line bg-surface p-4">
        <div className="flex items-center gap-2">
          <div className="min-w-0 flex-1">
            <h2 className="text-base font-semibold">Очередь загрузки</h2>
            <p className="text-sm text-ink-muted">{ordered.length > 0 ? summaryText(totals) : 'В очереди пусто'}</p>
          </div>
          <button type="button" className={secondary} disabled={running || totals.failed === 0} onClick={() => void run()}>
            Повторить ошибки
          </button>
          <button type="button" className={secondary} disabled={!running} onClick={() => void uploadApi.pause()}>
            Пауза
          </button>
          <button
            type="button"
            disabled={running || !waiting || batch === null}
            onClick={() => void run()}
            className="h-8 rounded-md bg-accent px-4 text-sm font-medium text-accent-ink outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 disabled:opacity-50"
          >
            Загрузить
          </button>
        </div>

        <div className="flex items-center gap-3">
          <div className="flex-1">
            <Bar fraction={fractionOf(totals)} label="Общий прогресс" />
          </div>
          <span className="text-sm text-ink-muted">{ordered.length > 0 ? progressText(totals) : ''}</span>
        </div>

        <div className="min-h-0 flex-1 overflow-auto">
          <table className="w-full table-fixed border-collapse text-sm">
            <colgroup>
              {COLUMNS.map(({ field, width }) => (
                <col key={field} style={width ? { width } : undefined} />
              ))}
            </colgroup>
            <thead className="sticky top-0 z-10 bg-surface text-left text-xs text-ink-muted">
              <tr>
                {COLUMNS.map(({ field, title }) => (
                  <th
                    key={field}
                    className="border-b border-line px-2 py-2 font-medium"
                    aria-sort={sort.field === field ? (sort.descending ? 'descending' : 'ascending') : 'none'}
                  >
                    <button type="button" onClick={() => sortBy(field)} className="outline-none hover:text-ink focus-visible:ring-2 focus-visible:ring-accent">
                      {title}
                      {sort.field === field ? (sort.descending ? ' ▼' : ' ▲') : ''}
                    </button>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {visible.map((row) => {
                const view = STATE_VIEWS[row.state]
                return (
                  <tr key={row.filename} className="border-b border-line" style={{ height: THUMB_SIDE + ROW_PADDING }}>
                    <td className="px-2">
                      <div className="flex items-center gap-2">
                        <Thumb key={row.path} path={row.path} side={THUMB_SIDE} />
                        <span className="min-w-0 flex-1 truncate font-mono text-xs" title={row.path} onDoubleClick={() => open(row.path)}>
                          {row.filename}
                        </span>
                      </div>
                    </td>
                    <td className="px-2 text-ink-muted">{formatSize(row.size)}</td>
                    <td className="px-2 pr-4">
                      <Bar fraction={row.progress} label={`Прогресс ${row.filename}`} />
                    </td>
                    <td className="px-2">
                      <span className="inline-flex items-center gap-2">
                        <span aria-hidden className={`size-2.5 rounded-full ${view.dot}`} />
                        {view.label}
                      </span>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>

        <Pager total={ordered.length} pageSize={PAGE_SIZE} page={shownPage} onPage={setPage} />
        <div className="flex items-center gap-3">
          <p aria-live="polite" className={`min-h-4 flex-1 truncate text-xs ${notice?.error ? 'text-danger' : 'text-ink-muted'}`}>
            {notice?.text}
          </p>
          {finished && (
            <button type="button" className={secondary} onClick={() => navigate('csv')}>
              Дальше: CSV и отправка →
            </button>
          )}
        </div>
      </section>
    </div>
  )
}
