import { useState } from 'react'
import { CATEGORY_OPTIONS } from '../../lib/categories'
import { EDITABLE_STATUSES } from '../../lib/describe'
import { viewOf } from '../../lib/status'

const MIN_BULK = 2 // below this a file is edited in the side panel instead

const selectClass =
  'h-8 rounded-md border border-line-strong bg-surface px-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:opacity-50'

interface Props {
  marked: number
  total: number
  onToggleAll: () => void
  /** Keyword to add or '', category code or null, status or null. */
  onApply: (tag: string, category: string | null, status: string | null) => void
  /** Tiles have no header, so the select-all checkbox lives here. */
  showSelectAll: boolean
}

/** A keyword is added to every marked file; the category and the status replace theirs. Empty fields change nothing. */
export function BulkBar({ marked, total, onToggleAll, onApply, showSelectAll }: Props) {
  const [tag, setTag] = useState('')
  const [category, setCategory] = useState('')
  const [status, setStatus] = useState('')
  const enabled = marked >= MIN_BULK

  const apply = () => {
    const word = tag.trim()
    if (!enabled || (!word && !category && !status)) return
    onApply(word, category || null, status || null)
    setTag('')
    setCategory('')
    setStatus('')
  }

  return (
    <div className="flex items-center gap-2 rounded-md bg-sunken px-3 py-2">
      {showSelectAll && (
        <input
          type="checkbox"
          aria-label="Отметить все"
          checked={total > 0 && marked === total}
          ref={(box) => {
            if (box) box.indeterminate = marked > 0 && marked < total
          }}
          onChange={onToggleAll}
          className="size-4 accent-accent"
        />
      )}
      <span className="whitespace-nowrap text-sm font-semibold">Выбрано {marked}</span>
      <input
        value={tag}
        disabled={!enabled}
        onChange={(event) => setTag(event.target.value)}
        onKeyDown={(event) => event.key === 'Enter' && apply()}
        placeholder="Тег для всех — Enter"
        className="h-8 min-w-0 flex-1 select-text rounded-md border border-line-strong bg-surface px-3 text-sm outline-none placeholder:text-ink-muted focus-visible:ring-2 focus-visible:ring-accent disabled:opacity-50"
      />
      <select value={category} disabled={!enabled} onChange={(event) => setCategory(event.target.value)} className={selectClass}>
        <option value="">Категория</option>
        {CATEGORY_OPTIONS.map(({ code, name }) => (
          <option key={code} value={code}>
            {name}
          </option>
        ))}
      </select>
      <select value={status} disabled={!enabled} onChange={(event) => setStatus(event.target.value)} className={selectClass}>
        <option value="">Статус</option>
        {EDITABLE_STATUSES.map((key) => (
          <option key={key} value={key}>
            {viewOf(key).label}
          </option>
        ))}
      </select>
      <button
        type="button"
        disabled={!enabled}
        onClick={apply}
        className="h-8 rounded-md border border-line-strong bg-surface px-3 text-sm outline-none hover:bg-window focus-visible:ring-2 focus-visible:ring-accent disabled:opacity-50"
      >
        Пометить
      </button>
    </div>
  )
}
