import { useState } from 'react'
import { parseKeywords } from '../lib/describe'

interface Props {
  tags: string[]
  onChange: (tags: string[]) => void
  disabled?: boolean
}

interface ChipProps {
  tag: string
  onRename: (value: string) => void
  onRemove: () => void
  disabled?: boolean
}

function Chip({ tag, onRename, onRemove, disabled }: ChipProps) {
  const [editing, setEditing] = useState(false)
  const finish = (value: string) => {
    setEditing(false)
    if (value.trim() !== tag) onRename(value.trim()) // an empty text means: remove
  }
  if (editing) {
    return (
      <input
        autoFocus
        defaultValue={tag}
        aria-label={`Изменить тег ${tag}`}
        onFocus={(event) => event.target.select()}
        onBlur={(event) => finish(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Enter') event.currentTarget.blur()
          if (event.key === 'Escape') setEditing(false)
        }}
        className="h-7 w-32 select-text rounded-full border border-accent bg-surface px-3 text-sm outline-none"
      />
    )
  }
  return (
    <span className="inline-flex h-7 max-w-full items-center rounded-full bg-sunken pl-3 pr-1 text-sm">
      <span
        className="max-w-[200px] truncate"
        title={`${tag}\nДвойной клик: изменить`}
        onDoubleClick={() => !disabled && setEditing(true)}
      >
        {tag}
      </span>
      <button
        type="button"
        aria-label={`Удалить тег ${tag}`}
        title="Удалить"
        disabled={disabled}
        onClick={onRemove}
        className="ml-1 flex size-5 items-center justify-center rounded-full text-ink-muted outline-none hover:bg-line hover:text-ink focus-visible:ring-2 focus-visible:ring-accent disabled:opacity-40"
      >
        ×
      </button>
    </span>
  )
}

/** Keywords as chips: × removes one, a double click renames it, new ones are typed below and added with Enter. */
export function TagEditor({ tags, onChange, disabled }: Props) {
  const [draft, setDraft] = useState('')
  const rename = (old: string, next: string) =>
    onChange(parseKeywords(tags.map((tag) => (tag === old ? next : tag)).join(',')))
  const add = () => {
    const typed = parseKeywords(draft)
    setDraft('')
    if (typed.length) onChange(parseKeywords([...tags, ...typed].join(',')))
  }
  return (
    <div className="flex flex-col gap-1.5">
      <span className="text-sm text-ink-muted">Ключевые слова · {tags.length}</span>
      <div className="flex flex-wrap gap-1.5">
        {tags.map((tag) => (
          <Chip
            key={tag}
            tag={tag}
            disabled={disabled}
            onRename={(next) => rename(tag, next)}
            onRemove={() => onChange(tags.filter((other) => other !== tag))}
          />
        ))}
      </div>
      <input
        value={draft}
        disabled={disabled}
        placeholder="Новый тег — Enter"
        onChange={(event) => setDraft(event.target.value)}
        onKeyDown={(event) => event.key === 'Enter' && add()}
        className="h-9 w-full select-text rounded-md border border-line-strong bg-surface px-3 text-sm outline-none placeholder:text-ink-muted focus-visible:ring-2 focus-visible:ring-accent disabled:opacity-50"
      />
    </div>
  )
}
