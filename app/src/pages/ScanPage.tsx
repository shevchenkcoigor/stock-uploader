import { listen } from '@tauri-apps/api/event'
import { open } from '@tauri-apps/plugin-dialog'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Button } from '../components/form'
import { Pager } from '../components/Pager'
import { StatusChip } from '../components/StatusChip'
import { Thumb } from '../components/Thumb'
import { filesApi, prefsApi, scanApi, type ProblemFile, type ScanProgress } from '../lib/api'
import { formatCount, humanizeReason, parseLastScan, shortPath } from '../lib/format'
import { viewOf } from '../lib/status'
import { DATA_CHANGED_EVENT } from '../lib/events'
import { SETTINGS_SAVED_EVENT } from '../lib/useSettings'
import type { PageProps } from './pages'

const PROBLEMS_PAGE_SIZE = 10
const PERCENT = 100
const RECURSIVE_KEY = 'scan_recursive'
const FOLDER_KEY = 'image_folder'
const LAST_SCAN_KEY = 'last_scan'

type SortField = 'file' | 'reason' | 'status'
type Counts = Record<'found' | 'new' | 'known' | 'problems', number | null>
const NO_COUNTS: Counts = { found: null, new: null, known: null, problems: null }
const TILES: ReadonlyArray<{ key: keyof Counts; title: string }> = [
  { key: 'found', title: 'Найдено' },
  { key: 'new', title: 'Новых' },
  { key: 'known', title: 'Уже в базе' },
  { key: 'problems', title: 'С проблемами' },
]
const COLUMNS: ReadonlyArray<{ field: SortField; title: string; className?: string }> = [
  { field: 'file', title: 'Файл' },
  { field: 'reason', title: 'Причина' },
  { field: 'status', title: 'Статус', className: 'w-[170px]' },
]

function Panel({ title, actions, children }: { title: string; actions?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section className="rounded-lg border border-line bg-surface p-4">
      <div className="mb-3 flex items-center justify-between gap-3">
        <h2 className="text-base font-semibold">{title}</h2>
        <div className="flex items-center gap-3">{actions}</div>
      </div>
      {children}
    </section>
  )
}

const sortKey = (field: SortField, row: ProblemFile): string | number[] => {
  if (field === 'file') return row.path.toLowerCase()
  if (field === 'reason') return humanizeReason(row.error).toLowerCase()
  return [viewOf(row.status).rank]
}

function sortProblems(rows: ProblemFile[], field: SortField, descending: boolean): ProblemFile[] {
  const direction = descending ? -1 : 1
  return [...rows].sort((a, b) => {
    const [left, right] = [sortKey(field, a), sortKey(field, b)]
    const order = typeof left === 'string' ? left.localeCompare(String(right)) : (left[0] ?? 0) - ((right as number[])[0] ?? 0)
    return (order || a.path.toLowerCase().localeCompare(b.path.toLowerCase())) * direction
  })
}

export function ScanPage({ active, navigate }: PageProps) {
  const [folder, setFolder] = useState('')
  const [recursive, setRecursive] = useState(true)
  const [running, setRunning] = useState(false)
  const [progress, setProgress] = useState<ScanProgress | null>(null)
  const [counts, setCounts] = useState<Counts>(NO_COUNTS)
  const [message, setMessage] = useState('')
  const [summary, setSummary] = useState('')
  const [problems, setProblems] = useState<ProblemFile[]>([])
  const [sort, setSort] = useState<{ field: SortField; descending: boolean }>({ field: 'status', descending: false })
  const [page, setPage] = useState(0)

  const refreshProblems = useCallback(() => {
    scanApi
      .problemFiles()
      .then(setProblems)
      .catch((reason) => setMessage(String(reason)))
  }, [])

  const loadSaved = useCallback(() => {
    void prefsApi.get(FOLDER_KEY, '').then(setFolder)
    void prefsApi.get(RECURSIVE_KEY, '1').then((value) => setRecursive(value === '1'))
    void prefsApi.get(LAST_SCAN_KEY, '{}').then((raw) => {
      const saved = parseLastScan(raw)
      if (saved) setCounts({ ...NO_COUNTS, ...saved.counts })
    })
  }, [])

  useEffect(() => {
    loadSaved()
    refreshProblems()
    const unlisten = listen<ScanProgress>('scan-progress', (event) => setProgress(event.payload))
    window.addEventListener(SETTINGS_SAVED_EVENT, refreshProblems)
    return () => {
      void unlisten.then((stop) => stop())
      window.removeEventListener(SETTINGS_SAVED_EVENT, refreshProblems)
    }
  }, [loadSaved, refreshProblems])

  useEffect(() => {
    if (active) refreshProblems()
  }, [active, refreshProblems])

  const browse = async () => {
    const chosen = await open({ directory: true, multiple: false, defaultPath: folder || undefined })
    if (typeof chosen === 'string') setFolder(chosen)
  }

  const run = async () => {
    setMessage('')
    setSummary('')
    setProgress(null)
    setRunning(true)
    try {
      const outcome = await scanApi.start(folder.trim(), recursive)
      if (!outcome) {
        setMessage('JPEG-файлы не найдены.')
        setSummary('Нет файлов')
        return
      }
      setCounts({ found: outcome.found, new: outcome.new, known: outcome.known, problems: outcome.problems })
      setSummary(`${outcome.stopped ? 'Остановлено' : 'Готово'} · ${formatCount(outcome.found)} файлов`)
      const changed = outcome.changedAfterUpload.map((name) => `ВНИМАНИЕ ${name} изменён после загрузки, повторно не грузится`)
      if (changed.length) setMessage(changed.join('\n'))
      window.dispatchEvent(new Event(DATA_CHANGED_EVENT))
      refreshProblems()
    } catch (reason) {
      setMessage(String(reason))
    } finally {
      setRunning(false)
      setProgress(null)
    }
  }

  const ordered = useMemo(() => sortProblems(problems, sort.field, sort.descending), [problems, sort])
  const lastPage = Math.max(0, Math.ceil(ordered.length / PROBLEMS_PAGE_SIZE) - 1)
  const shownPage = Math.min(page, lastPage)
  const shown = ordered.slice(shownPage * PROBLEMS_PAGE_SIZE, (shownPage + 1) * PROBLEMS_PAGE_SIZE)

  const sortBy = (field: SortField) => {
    setSort((current) => ({ field, descending: current.field === field && !current.descending }))
    setPage(0)
  }

  const percent = progress && progress.total > 0 ? Math.floor((progress.done * PERCENT) / progress.total) : null
  const progressText =
    progress && progress.total > 0
      ? `${formatCount(progress.done)} из ${formatCount(progress.total)} · ${percent}%`
      : (progress?.message ?? summary)

  return (
    <div className="flex h-full flex-col gap-4 overflow-y-auto p-6">
      <Panel title="Папка с изображениями">
        <div className="flex gap-2">
          <input
            value={folder}
            onChange={(event) => setFolder(event.target.value)}
            placeholder="/путь/к/папке с JPEG"
            spellCheck={false}
            className="h-9 min-w-0 flex-1 select-text rounded-md border border-line-strong bg-surface px-3 font-mono text-xs outline-none placeholder:text-ink-muted focus-visible:ring-2 focus-visible:ring-accent"
          />
          <Button onClick={() => void browse()} disabled={running}>
            Выбрать папку…
          </Button>
          <button
            type="button"
            onClick={() => void run()}
            disabled={running || !folder.trim()}
            className="h-9 rounded-md bg-accent px-4 text-sm font-medium text-accent-ink outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 disabled:opacity-50"
          >
            Сканировать
          </button>
        </div>
        <label className="mt-3 flex items-center gap-2 text-sm">
          <input type="checkbox" checked={recursive} disabled={running} onChange={(event) => setRecursive(event.target.checked)} className="size-4 accent-accent" />
          Включая вложенные папки
        </label>
        {message && <p className="mt-3 whitespace-pre-line select-text text-sm text-danger">{message}</p>}
      </Panel>

      <Panel
        title="Сканирование"
        actions={
          <>
            <span className="text-sm text-ink-muted">{progressText}</span>
            <Button onClick={() => void scanApi.cancel()} disabled={!running}>
              Остановить
            </Button>
          </>
        }
      >
        <div className="h-2 overflow-hidden rounded-full bg-sunken" role="progressbar" aria-valuenow={percent ?? undefined}>
          <div
            className={`h-full rounded-full bg-accent transition-[width] ${running && percent === null ? 'w-1/3 animate-pulse' : ''}`}
            style={percent !== null ? { width: `${percent}%` } : summary.startsWith('Готово') ? { width: '100%' } : undefined}
          />
        </div>
        <p className="mt-2 h-4 truncate font-mono text-xs text-ink-muted">{progress && progress.total > 0 ? shortPath(progress.message, folder) : ''}</p>
        <div className="mt-3 grid grid-cols-4 gap-3">
          {TILES.map(({ key, title }) => (
            <div key={key} className="rounded-md bg-sunken px-3 py-2.5">
              <div className="text-xs text-ink-muted">{title}</div>
              <div className={`text-2xl font-semibold ${key === 'problems' && counts.problems ? 'text-danger' : ''}`}>
                {counts[key] === null ? '—' : formatCount(counts[key])}
              </div>
            </div>
          ))}
        </div>
      </Panel>

      <Panel
        title="Проблемные файлы"
        actions={
          <button
            type="button"
            onClick={() => navigate('describe')}
            className="h-9 rounded-md bg-accent px-4 text-sm font-medium text-accent-ink outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2"
          >
            К описанию →
          </button>
        }
      >
        <table className="w-full table-fixed border-collapse text-sm">
          <thead className="text-left text-xs text-ink-muted">
            <tr>
              {COLUMNS.map(({ field, title, className }) => (
                <th key={field} className={`pb-2 font-medium ${className ?? ''}`} aria-sort={sort.field === field ? (sort.descending ? 'descending' : 'ascending') : 'none'}>
                  <button type="button" onClick={() => sortBy(field)} className="outline-none hover:text-ink focus-visible:ring-2 focus-visible:ring-accent">
                    {title}
                    {sort.field === field ? (sort.descending ? ' ▼' : ' ▲') : ''}
                  </button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {shown.map((row) => (
              <tr key={row.path} className="group border-t border-line" onDoubleClick={() => void filesApi.open(row.path).catch((reason) => setMessage(String(reason)))}>
                <td className="py-1.5 pr-3">
                  <div className="flex items-center gap-3">
                    <Thumb key={row.path} path={row.path} badge={row.status === 'review' ? 'bg-warning' : undefined} />
                    <span className="min-w-0 flex-1 truncate font-mono text-xs" title={row.path}>
                      {shortPath(row.path, folder)}
                    </span>
                    <button
                      type="button"
                      onClick={() => void filesApi.reveal(row.path).catch((reason) => setMessage(String(reason)))}
                      className="shrink-0 rounded px-1.5 text-xs text-ink-muted opacity-0 outline-none hover:text-ink focus-visible:opacity-100 focus-visible:ring-2 focus-visible:ring-accent group-hover:opacity-100"
                    >
                      Показать
                    </button>
                  </div>
                </td>
                <td className="select-text py-1.5 pr-3" title={row.error}>
                  {humanizeReason(row.error)}
                </td>
                <td className="py-1.5">
                  <StatusChip status={row.status} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {problems.length === 0 && <p className="py-6 text-center text-sm text-ink-muted">Проблемных файлов нет</p>}
        <div className="mt-3">
          <Pager total={ordered.length} pageSize={PROBLEMS_PAGE_SIZE} page={shownPage} onPage={setPage} />
        </div>
      </Panel>
    </div>
  )
}
