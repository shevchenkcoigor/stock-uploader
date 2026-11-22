import { listen } from '@tauri-apps/api/event'
import { ask } from '@tauri-apps/plugin-dialog'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Pager } from '../components/Pager'
import { Thumb } from '../components/Thumb'
import {
  filesApi,
  uploadApi,
  type PrepareProgress,
  type QueueFile,
  type UploadProgress,
} from '../lib/api'
import { formatSize } from '../lib/describe'
import { DATA_CHANGED_EVENT } from '../lib/events'
import {
  STATE_VIEWS,
  fractionOf,
  prepareTrouble,
  progressText,
  rowsFromQueue,
  sortedRows,
  summaryText,
  totalsOf,
  uploadHint,
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

function Bar({ fraction, label }: { fraction: number; label: string }) {
  const percent = Math.round(fraction * PERCENT)
  return (
    <div className="h-2 overflow-hidden rounded-full bg-sunken" role="progressbar" aria-label={label} aria-valuenow={percent}>
      <div className="h-full bg-accent" style={{ width: `${percent}%` }} />
    </div>
  )
}

export function UploadPage({ active }: PageProps) {
  const [files, setFiles] = useState<QueueFile[]>([])
  const [running, setRunning] = useState(false)
  const [current, setCurrent] = useState<Current | null>(null)
  const [doneCount, setDoneCount] = useState(0)
  const [sort, setSort] = useState<{ field: UploadSort; descending: boolean }>({ field: 'status', descending: false })
  const [page, setPage] = useState(0)
  const [notice, setNotice] = useState<{ text: string; error: boolean } | null>(null)

  const say = useCallback((text: string, error = false) => setNotice({ text, error }), [])

  const loadQueue = useCallback(() => {
    uploadApi
      .queue()
      .then(setFiles)
      .catch((reason) => say(String(reason), true))
  }, [say])

  useEffect(() => {
    loadQueue()
    window.addEventListener(DATA_CHANGED_EVENT, loadQueue)
    return () => window.removeEventListener(DATA_CHANGED_EVENT, loadQueue)
  }, [loadQueue])
  useEffect(() => {
    if (active && !running) loadQueue()
  }, [active, running, loadQueue])
  // the queue follows every finished file of a run
  useEffect(loadQueue, [loadQueue, doneCount])

  useEffect(() => {
    const unlisten = listen<UploadProgress>('upload-progress', ({ payload }) => {
      setCurrent({ file: payload.file, sent: payload.sent, size: payload.size })
      setDoneCount(payload.done)
      say(payload.message, payload.message.startsWith('FAILED'))
    })
    return () => void unlisten.then((stop) => stop())
  }, [say])

  useEffect(() => {
    const unlisten = listen<PrepareProgress>('prepare-progress', ({ payload }) => {
      if (payload.file) say(`Готовлю ${payload.file}: ${payload.done + 1} из ${payload.total}`)
    })
    return () => void unlisten.then((stop) => stop())
  }, [say])

  // the copies with the title and keywords embedded are made first (a no-op when they are current), then sent
  const run = async () => {
    if (running) return
    setRunning(true)
    setCurrent(null)
    try {
      say('Готовлю файлы: вшиваю название и ключевые слова…')
      const trouble = prepareTrouble(await uploadApi.prepare())
      if (trouble) {
        say(trouble, true)
        return
      }
      say(KEYCHAIN_NOTICE)
      const report = await uploadApi.start()
      if (report.blocked) {
        say(report.blocked, true)
      } else if (report.error) {
        say(`Не удалось загрузить: ${report.error}. Файлы остались в очереди: проверьте SFTP в «Настройках».`, true)
      } else {
        const failed = report.failed.length
        const parts = [`Загружено: ${report.uploaded} из ${report.total}, ошибок: ${failed}`]
        if (report.stopped) parts.push('приостановлено')
        say(parts.join(' · '), failed > 0)
      }
    } catch (reason) {
      say(`ОШИБКА: ${reason}`, true)
    } finally {
      setRunning(false)
      setCurrent(null)
      window.dispatchEvent(new Event(DATA_CHANGED_EVENT))
    }
  }

  const markSubmitted = async () => {
    const agreed = await ask(
      'Вы уже выбрали категории в портале и отправили загруженные файлы на проверку?\nВсе файлы со статусом «На SFTP» будут помечены как отправленные.',
      { title: 'Отметить отправленным', kind: 'warning' },
    )
    if (!agreed) return
    setRunning(true)
    try {
      const report = await uploadApi.markSubmitted()
      say(`Отмечено отправленными: ${report.count}`, report.count === 0)
    } catch (reason) {
      say(`ОШИБКА: ${reason}`, true)
    } finally {
      setRunning(false)
      window.dispatchEvent(new Event(DATA_CHANGED_EVENT))
    }
  }

  const rows = useMemo(() => rowsFromQueue(files, running, current), [files, running, current])
  const totals = totalsOf(rows)
  const ordered = useMemo(() => sortedRows(rows, sort.field, sort.descending), [rows, sort])
  const shownPage = Math.min(page, Math.max(0, Math.ceil(ordered.length / PAGE_SIZE) - 1))
  const visible = ordered.slice(shownPage * PAGE_SIZE, (shownPage + 1) * PAGE_SIZE)
  const waiting = totals.done < totals.total
  const hint = uploadHint(totals)

  const sortBy = (field: UploadSort) => {
    setSort((now) => ({ field, descending: now.field === field && !now.descending }))
    setPage(0)
  }
  const open = (path: string) => void filesApi.open(path).catch(() => undefined)

  return (
    <div className="flex h-full flex-col gap-4 p-6">
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
            disabled={running || !waiting}
            title={hint}
            onClick={() => void run()}
            className="h-8 rounded-md bg-accent px-4 text-sm font-medium text-accent-ink outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 disabled:opacity-50"
          >
            Загрузить
          </button>
          <button
            type="button"
            className={secondary}
            disabled={running || totals.uploaded === 0}
            title="Когда категории выбраны в портале и загруженные файлы отправлены на проверку"
            onClick={() => void markSubmitted()}
          >
            Отметить отправленным
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
        {!waiting && totals.uploaded > 0 && !running && (
          <p className="rounded-md bg-sunken px-3 py-2 text-sm">
            Все файлы на SFTP. Название и ключевые слова уже вшиты в файлы. Дальше в портале Adobe Stock: Uploaded Files →
            категория, «Created using generative AI tools» для всех файлов и «People and Property are fictional» там, где есть
            люди; отправьте на проверку и нажмите «Отметить отправленным».
          </p>
        )}
        <div className="flex items-center gap-3">
          <p aria-live="polite" className={`min-h-4 flex-1 truncate text-xs ${notice?.error ? 'text-danger' : 'text-ink-muted'}`}>
            {notice?.text}
          </p>
        </div>
      </section>
    </div>
  )
}
