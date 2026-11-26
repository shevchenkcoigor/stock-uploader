import { useState } from 'react'

interface Props {
  value: string
  placeholder: string
  twoLines?: boolean // wrap onto a second line before cutting off, where the row is tall enough
  onCommit: (value: string) => void
}

/** Text in a table cell; a double click turns it into an input (Enter or leaving saves, Escape cancels). */
export function EditableCell({ value, placeholder, twoLines, onCommit }: Props) {
  const [editing, setEditing] = useState(false)
  if (editing) {
    return (
      <input
        autoFocus
        defaultValue={value}
        aria-label={placeholder}
        onFocus={(event) => event.target.select()}
        onBlur={(event) => {
          setEditing(false)
          if (event.target.value !== value) onCommit(event.target.value)
        }}
        onKeyDown={(event) => {
          if (event.key === 'Enter') event.currentTarget.blur()
          if (event.key === 'Escape') setEditing(false)
        }}
        className="h-8 w-full select-text rounded border border-accent bg-surface px-2 text-sm outline-none"
      />
    )
  }
  return (
    <span
      tabIndex={0}
      title="Двойной клик: изменить"
      onDoubleClick={() => setEditing(true)}
      onKeyDown={(event) => event.key === 'Enter' && setEditing(true)}
      className={`${twoLines ? 'line-clamp-2 break-words' : 'block truncate py-1'} rounded px-2 outline-none focus-visible:ring-2 focus-visible:ring-accent ${
        value ? '' : 'italic text-ink-muted'
      }`}
    >
      {value || placeholder}
    </span>
  )
}
