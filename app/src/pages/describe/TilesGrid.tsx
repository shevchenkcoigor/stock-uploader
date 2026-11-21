import { Thumb } from '../../components/Thumb'
import type { FileRow } from '../../lib/api'
import { viewOf } from '../../lib/status'
import type { Modifiers } from './useDescribe'

interface Props {
  rows: FileRow[]
  selected: string
  marked: ReadonlySet<string>
  tileSide: number
  onSelect: (filename: string, modifiers: Modifiers) => void
  onToggleMark: (filename: string) => void
  onOpen: (path: string) => void
}

export function TilesGrid({ rows, selected, marked, tileSide, onSelect, onToggleMark, onOpen }: Props) {
  return (
    <ul className="flex flex-wrap gap-2 p-2">
      {rows.map((row) => {
        const view = viewOf(row.status)
        return (
          <li key={row.filename} style={{ width: tileSide + 24 }}>
            <div
              role="button"
              tabIndex={0}
              aria-pressed={row.filename === selected}
              title={`${row.filename}\n${view.label}`}
              onClick={(event) => onSelect(row.filename, { meta: event.metaKey || event.ctrlKey, shift: event.shiftKey })}
              onDoubleClick={() => onOpen(row.path)}
              onKeyDown={(event) => event.key === 'Enter' && onSelect(row.filename, { meta: false, shift: false })}
              className={`relative flex flex-col items-center gap-1 rounded-md p-2 text-center outline-none focus-visible:ring-2 focus-visible:ring-accent ${
                row.filename === selected ? 'bg-accent-soft' : marked.has(row.filename) ? 'bg-sunken' : 'hover:bg-sunken'
              }`}
            >
              <input
                type="checkbox"
                aria-label={`Отметить ${row.filename}`}
                checked={marked.has(row.filename)}
                onClick={(event) => event.stopPropagation()}
                onChange={() => onToggleMark(row.filename)}
                className="absolute left-2 top-2 z-10 size-4 accent-accent"
              />
              <Thumb key={row.path} path={row.path} side={tileSide} badge={view.dot} />
              <span className="w-full truncate font-mono text-xs">{row.filename}</span>
              <span className="text-xs text-ink-muted">{view.label}</span>
            </div>
          </li>
        )
      })}
    </ul>
  )
}
