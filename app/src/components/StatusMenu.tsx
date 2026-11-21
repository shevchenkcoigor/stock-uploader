import { useEffect, useRef, useState } from 'react'
import { EDITABLE_STATUSES } from '../lib/describe'
import { viewOf } from '../lib/status'
import { StatusChip } from './StatusChip'

interface Props {
  status: string
  onPick: (status: string) => void
  disabled?: boolean
  /** Fill the width and show a frame, like a form field. */
  field?: boolean
}

/** The status as a chip; a click opens the list of statuses a person may set. */
export function StatusMenu({ status, onPick, disabled, field }: Props) {
  const [open, setOpen] = useState(false)
  const holder = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const close = (event: MouseEvent | KeyboardEvent) => {
      const outside =
        event instanceof KeyboardEvent ? event.key === 'Escape' : !holder.current?.contains(event.target as Node)
      if (outside) setOpen(false)
    }
    document.addEventListener('mousedown', close)
    document.addEventListener('keydown', close)
    return () => {
      document.removeEventListener('mousedown', close)
      document.removeEventListener('keydown', close)
    }
  }, [open])
  return (
    <div ref={holder} className={`relative ${field ? 'w-full' : 'inline-block'}`}>
      <button
        type="button"
        disabled={disabled}
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => setOpen((current) => !current)}
        className={`flex items-center rounded-md outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:opacity-50 ${
          field ? 'h-9 w-full border border-line-strong bg-surface px-3' : 'h-8 px-1.5 hover:bg-sunken'
        }`}
      >
        <StatusChip status={status} />
      </button>
      {open && (
        <ul
          role="listbox"
          className="absolute left-0 z-20 mt-1 min-w-44 rounded-md border border-line bg-surface py-1 shadow-lg"
        >
          {EDITABLE_STATUSES.map((key) => (
            <li key={key} role="option" aria-selected={key === status}>
              <button
                type="button"
                onClick={() => {
                  setOpen(false)
                  if (key !== status) onPick(key)
                }}
                className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm hover:bg-sunken"
              >
                <span aria-hidden className={`size-2.5 rounded-full ${viewOf(key).dot}`} />
                <span className="flex-1">{viewOf(key).label}</span>
                {key === status && <span aria-hidden>✓</span>}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
