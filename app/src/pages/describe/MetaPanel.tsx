import { useEffect, useRef, useState } from 'react'
import { StatusChip } from '../../components/StatusChip'
import { TagEditor } from '../../components/TagEditor'
import { filesApi, type FileRow, type ModelChoice } from '../../lib/api'
import { CATEGORY_OPTIONS } from '../../lib/categories'
import { viewOf } from '../../lib/status'
import { EDITABLE_STATUSES, RECAPTIONABLE, formatSize, modelTitle, resolutionText, type Dimensions } from '../../lib/describe'

const AUTOSAVE_DELAY_MS = 700 // a pause in typing the title that saves it
const PREVIEW_SIDE = 240
const PREVIEW_HEIGHT = 180

interface Draft {
  title: string | null
  keywords: string[] | null
  category: string | null
}
const NO_DRAFT: Draft = { title: null, keywords: null, category: null }

function Preview({ path }: { path: string }) {
  const [url, setUrl] = useState<string | null | undefined>(undefined)
  useEffect(() => {
    let current = true
    filesApi
      .thumbnail(path, PREVIEW_SIDE)
      .then((found) => current && setUrl(found))
      .catch(() => current && setUrl(null))
    return () => {
      current = false
    }
  }, [path])
  return (
    <div
      style={{ height: PREVIEW_HEIGHT }}
      className="flex items-center justify-center overflow-hidden rounded-md border border-line bg-sunken text-sm text-ink-muted"
    >
      {url ? <img src={url} alt="" className="size-full object-contain" /> : url === null ? 'Нет превью' : ''}
    </div>
  )
}

const fieldLabel = 'text-sm text-ink-muted'
const inputClass =
  'h-9 w-full select-text rounded-md border border-line-strong bg-surface px-3 text-sm outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:opacity-50'

interface Props {
  row: FileRow | null
  dims: Dimensions
  hasPrev: boolean
  hasNext: boolean
  onSave: (filename: string, title: string, keywords: string[], category: string) => void
  onStatus: (filename: string, status: string) => void
  onApprove: (filename: string) => void
  onStep: (delta: number) => void
  caption: {
    models: ModelChoice[]
    model: string
    running: boolean
    onModel: (model: string) => void
    onRun: (row: FileRow) => void
  }
}

/** The card of the open file. The parent gives it a `key` of the file name: another file, a fresh card. */
export function MetaPanel({ row, dims, hasPrev, hasNext, onSave, onStatus, onApprove, onStep, caption }: Props) {
  const canCaption = row !== null && RECAPTIONABLE.includes(row.status)
  const [draft, setDraft] = useState<Draft>(NO_DRAFT)
  const [seen, setSeen] = useState(row)
  const timer = useRef<number | undefined>(undefined)
  const commitRef = useRef<() => void>(() => undefined)

  // A refresh brings the stored values; they replace the draft except for a title that is still being typed.
  if (row !== seen) {
    setSeen(row)
    setDraft((current) => ({
      title: current.title !== null && row && current.title.trim() !== row.title ? current.title : null,
      keywords: null,
      category: null,
    }))
  }

  const title = draft.title ?? row?.title ?? ''
  const keywords = draft.keywords ?? row?.keywords ?? []
  const category = draft.category ?? row?.category ?? ''

  const commit = (next: Partial<Draft>) => {
    if (!row) return
    window.clearTimeout(timer.current)
    timer.current = undefined
    const merged = { ...draft, ...next }
    setDraft(merged)
    onSave(row.filename, (merged.title ?? row.title).trim(), merged.keywords ?? row.keywords, merged.category ?? row.category)
  }
  useEffect(() => {
    commitRef.current = () => commit({})
  })
  // What was typed in the card is saved before another file opens.
  useEffect(
    () => () => {
      if (timer.current !== undefined) commitRef.current()
    },
    [],
  )

  const typeTitle = (value: string) => {
    setDraft((current) => ({ ...current, title: value }))
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => commitRef.current(), AUTOSAVE_DELAY_MS)
  }

  const disabled = row === null
  const info = row ? [formatSize(row.size), resolutionText(dims[row.path])].filter(Boolean).join(' · ') : ''

  return (
    <aside className="flex h-full w-80 shrink-0 flex-col rounded-lg border border-line bg-surface p-4">
      <div className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto pr-1 [&>*]:shrink-0">
        <div className="flex items-center justify-between">
          <h2 className="text-base font-semibold">Метаданные</h2>
          {row && <StatusChip status={row.status} />}
        </div>
        {row ? <Preview key={row.path} path={row.path} /> : <div style={{ height: PREVIEW_HEIGHT }} className="flex items-center justify-center rounded-md border border-line bg-sunken text-sm text-ink-muted">Выберите файл</div>}
        <p className="truncate font-mono text-xs" title={row?.filename}>
          {row?.filename}
        </p>
        <p className="text-xs text-ink-muted">{info}</p>
        {row?.error && <p className="select-text text-sm text-warning">{row.error}</p>}

        <span className={fieldLabel}>Заголовок</span>
        <input
          value={title}
          disabled={disabled}
          onChange={(event) => typeTitle(event.target.value)}
          onBlur={() => timer.current !== undefined && commit({})}
          className={inputClass}
        />
        <TagEditor tags={keywords} disabled={disabled} onChange={(next) => commit({ keywords: next })} />
        <span className={fieldLabel}>Категория</span>
        <select value={category} disabled={disabled} onChange={(event) => commit({ category: event.target.value })} className={inputClass}>
          <option value="">—</option>
          {CATEGORY_OPTIONS.map(({ code, name }) => (
            <option key={code} value={code}>
              {name}
            </option>
          ))}
        </select>
        <span className={fieldLabel}>Статус</span>
        <select
          value={row?.status ?? ''}
          disabled={disabled}
          onChange={(event) => row && onStatus(row.filename, event.target.value)}
          className={inputClass}
        >
          {row && !EDITABLE_STATUSES.includes(row.status) && <option value={row.status}>{viewOf(row.status).label}</option>}
          {EDITABLE_STATUSES.map((key) => (
            <option key={key} value={key}>
              {viewOf(key).label}
            </option>
          ))}
        </select>
        <div className="flex gap-2">
          <select
            value={caption.model}
            aria-label="Модель Claude"
            disabled={disabled || caption.running}
            onChange={(event) => caption.onModel(event.target.value)}
            className="h-9 min-w-0 flex-1 rounded-md border border-line-strong bg-surface px-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:opacity-50"
          >
            {caption.models.map(({ id, note }) => (
              <option key={id} value={id} title={`${id} — ${note}`}>
                {modelTitle(id)}
              </option>
            ))}
          </select>
          <button
            type="button"
            disabled={!canCaption || caption.running || !caption.model}
            title={canCaption ? 'Подписать этот файл заново' : 'Отправленный файл не подписывают'}
            onClick={() => row && caption.onRun(row)}
            className="h-9 shrink-0 rounded-md bg-accent px-3 text-sm font-medium text-white outline-none hover:opacity-90 focus-visible:ring-2 focus-visible:ring-accent disabled:opacity-50"
          >
            {caption.running ? 'Подписываю…' : '✦ Caption'}
          </button>
        </div>
        {row?.status === 'review' && (
          <button
            type="button"
            onClick={() => onApprove(row.filename)}
            className="h-9 rounded-md border border-line-strong bg-surface text-sm outline-none hover:bg-sunken focus-visible:ring-2 focus-visible:ring-accent"
          >
            Одобрить файл
          </button>
        )}
      </div>
      <div className="mt-3 flex gap-2">
        {(
          [
            ['← Пред', -1, hasPrev],
            ['След →', 1, hasNext],
          ] as const
        ).map(([label, delta, enabled]) => (
          <button
            key={label}
            type="button"
            disabled={!enabled}
            onClick={() => onStep(delta)}
            className="h-9 flex-1 rounded-md border border-line-strong bg-surface text-sm outline-none hover:bg-sunken focus-visible:ring-2 focus-visible:ring-accent disabled:opacity-40"
          >
            {label}
          </button>
        ))}
      </div>
    </aside>
  )
}
