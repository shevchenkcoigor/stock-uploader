import { useCallback, useEffect, useState } from 'react'
import { api, type EventRow, type EventsPage } from '../lib/api'

const EVENTS_SHOWN = 300
const FOLLOW_INTERVAL_MS = 2000
const ALL_SECTIONS = 'Все разделы'
const LEVELS: ReadonlyArray<{ label: string; value: string | null }> = [
  { label: 'Все уровни', value: null },
  { label: 'Ошибки', value: 'error' },
  { label: 'Предупреждения', value: 'warning' },
  { label: 'Инфо', value: 'info' },
]
const LEVEL_CLASS: Record<string, string> = {
  error: 'text-danger',
  warning: 'text-warning',
  info: 'text-ink-muted',
}
const LEVEL_LABEL: Record<string, string> = { error: 'Ошибка', warning: 'Предупреждение', info: 'Инфо' }

// Timestamps are stored as UTC ISO strings; show them in local time.
const formatTime = (at: string) => new Date(at).toLocaleTimeString('ru-RU')
const messageText = (row: EventRow) => (row.filename ? `${row.filename}: ${row.message}` : row.message)

const controlClass =
  'h-8 rounded-md border border-line-strong bg-surface px-2 text-sm text-ink outline-none focus-visible:ring-2 focus-visible:ring-accent'

export function LogPage() {
  const [text, setText] = useState('')
  const [level, setLevel] = useState<string | null>(null)
  const [command, setCommand] = useState<string | null>(null)
  const [commands, setCommands] = useState<string[]>([])
  const [page, setPage] = useState<EventsPage>({ rows: [], total: 0 })
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(() => {
    Promise.all([api.searchEvents({ text, level, command, limit: EVENTS_SHOWN }), api.eventCommands()])
      .then(([events, names]) => {
        setPage(events)
        setCommands(names)
        setError(null)
      })
      .catch((reason) => setError(String(reason)))
  }, [text, level, command])

  useEffect(() => {
    load()
    const timer = window.setInterval(load, FOLLOW_INTERVAL_MS)
    return () => window.clearInterval(timer)
  }, [load])

  return (
    <div className="flex h-full flex-col gap-3 p-4">
      <div className="flex items-center gap-2">
        <input
          type="search"
          value={text}
          onChange={(event) => setText(event.target.value)}
          placeholder="Поиск по сообщению и файлу"
          className={`${controlClass} flex-1 select-text`}
        />
        <select
          value={level ?? ''}
          onChange={(event) => setLevel(event.target.value || null)}
          className={controlClass}
        >
          {LEVELS.map(({ label, value }) => (
            <option key={label} value={value ?? ''}>
              {label}
            </option>
          ))}
        </select>
        <select
          value={command ?? ''}
          onChange={(event) => setCommand(event.target.value || null)}
          className={controlClass}
        >
          <option value="">{ALL_SECTIONS}</option>
          {commands.map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </select>
      </div>

      {error && <p className="text-sm text-danger">{error}</p>}

      <div className="min-h-0 flex-1 overflow-auto rounded-md border border-line bg-surface">
        <table className="w-full table-fixed border-collapse text-sm">
          <thead className="sticky top-0 bg-sunken text-left text-xs text-ink-muted">
            <tr>
              <th className="w-[110px] px-3 py-2 font-medium">Время</th>
              <th className="w-[120px] px-3 py-2 font-medium">Уровень</th>
              <th className="w-[110px] px-3 py-2 font-medium">Раздел</th>
              <th className="px-3 py-2 font-medium">Сообщение</th>
            </tr>
          </thead>
          <tbody>
            {page.rows.map((row) => (
              <tr key={row.id} className="border-t border-line">
                <td className="px-3 py-1.5 font-mono text-xs text-ink-muted">{formatTime(row.at)}</td>
                <td className={`px-3 py-1.5 ${LEVEL_CLASS[row.level] ?? 'text-ink-muted'}`}>
                  {LEVEL_LABEL[row.level] ?? row.level}
                </td>
                <td className="px-3 py-1.5">{row.command}</td>
                <td className="select-text break-words px-3 py-1.5">{messageText(row)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {page.rows.length === 0 && !error && (
          <p className="p-6 text-center text-sm text-ink-muted">Событий нет</p>
        )}
      </div>

      <p className="text-xs text-ink-muted">
        Показано {page.rows.length} из {page.total}
      </p>
    </div>
  )
}
