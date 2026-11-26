import { EditableCell } from '../../components/EditableCell'
import { StatusMenu } from '../../components/StatusMenu'
import { Thumb } from '../../components/Thumb'
import type { FileRow } from '../../lib/api'
import { CATEGORY_OPTIONS } from '../../lib/categories'
import { formatSize, parseKeywords, resolutionText, type Dimensions, type SortField } from '../../lib/describe'
import type { Modifiers } from './useDescribe'

const NAME_COLUMN_BASE = 130 // room for the checkbox and a file name; the picture comes on top of it
// Narrowest widths of a table without resolution and size: title and keywords (100 each), category, status.
const OTHER_COLUMNS_MIN = 2 * 100 + 130 + 150
const ROW_PADDING = 8
const TWO_LINE_THUMB = 40 // from this thumbnail height a row holds two lines of text

const COLUMNS: ReadonlyArray<{ field: SortField; title: string }> = [
  { field: 'name', title: 'Имя' },
  { field: 'title', title: 'Заголовок' },
  { field: 'keywords', title: 'Ключевые слова' },
  { field: 'category', title: 'Категория' },
  { field: 'resolution', title: 'Разрешение' },
  { field: 'size', title: 'Размер' },
  { field: 'status', title: 'Статус' },
]
// Title and keywords share what is left. A table narrower than 1000px drops resolution and size (the card
// of the open file shows both) and narrows the category.
const COLUMN_CLASSES: Partial<Record<SortField, string>> = {
  category: 'w-[130px] @min-[1000px]:w-[170px]',
  resolution: 'w-[115px] @max-[1000px]:hidden',
  size: 'w-[80px] @max-[1000px]:hidden',
  status: 'w-[150px]',
}

interface Props {
  rows: FileRow[]
  selected: string
  marked: ReadonlySet<string>
  allState: 'none' | 'some' | 'all'
  dims: Dimensions
  thumbSide: number
  sort: { field: SortField; descending: boolean }
  onSort: (field: SortField) => void
  onSelect: (filename: string, modifiers: Modifiers) => void
  onToggleMark: (filename: string) => void
  onToggleAll: () => void
  onSave: (filename: string, title: string, keywords: string[], category: string) => void
  onStatus: (filename: string, status: string) => void
  onOpen: (path: string) => void
}

export function FilesTable(props: Props) {
  const { rows, selected, marked, allState, dims, thumbSide, sort, onSort } = props
  const nameWidth = NAME_COLUMN_BASE + thumbSide
  const twoLines = thumbSide >= TWO_LINE_THUMB
  return (
    <div className="@container">
      <table className="w-full table-fixed border-collapse text-sm" style={{ minWidth: nameWidth + OTHER_COLUMNS_MIN }}>
        <thead className="sticky top-0 z-10 bg-surface text-left text-xs text-ink-muted">
          <tr>
            {COLUMNS.map(({ field, title }) => (
              <th
                key={field}
                className={`border-b border-line px-2 py-2 font-medium ${COLUMN_CLASSES[field] ?? ''}`}
                style={field === 'name' ? { width: nameWidth } : undefined}
                aria-sort={sort.field === field ? (sort.descending ? 'descending' : 'ascending') : 'none'}
              >
                <div className="flex items-center gap-2">
                  {field === 'name' && (
                    <input
                      type="checkbox"
                      aria-label="Отметить все"
                      checked={allState === 'all'}
                      ref={(box) => {
                        if (box) box.indeterminate = allState === 'some'
                      }}
                      onChange={props.onToggleAll}
                      className="size-4 accent-accent"
                    />
                  )}
                  <button type="button" onClick={() => onSort(field)} className="text-left outline-none hover:text-ink focus-visible:ring-2 focus-visible:ring-accent">
                    {title}
                    {sort.field === field ? (sort.descending ? ' ▼' : ' ▲') : ''}
                  </button>
                </div>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr
              key={row.filename}
              aria-selected={row.filename === selected}
              onClick={(event) => props.onSelect(row.filename, { meta: event.metaKey || event.ctrlKey, shift: event.shiftKey })}
              className={`border-b border-line ${row.filename === selected ? 'bg-accent-soft' : marked.has(row.filename) ? 'bg-sunken' : 'hover:bg-sunken'}`}
              style={{ height: thumbSide + ROW_PADDING }}
            >
              <td className="px-2">
                <div className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    aria-label={`Отметить ${row.filename}`}
                    checked={marked.has(row.filename)}
                    onClick={(event) => event.stopPropagation()}
                    onChange={() => props.onToggleMark(row.filename)}
                    className="size-4 shrink-0 accent-accent"
                  />
                  <Thumb key={row.path} path={row.path} side={thumbSide} />
                  <span
                    className="min-w-0 flex-1 truncate font-mono text-xs"
                    title={row.path}
                    onDoubleClick={() => props.onOpen(row.path)}
                  >
                    {row.filename}
                  </span>
                </div>
              </td>
              <td className="px-1" onClick={(event) => event.stopPropagation()}>
                <EditableCell
                  value={row.title}
                  placeholder="Нет заголовка"
                  twoLines={twoLines}
                  onCommit={(title) => props.onSave(row.filename, title.trim(), row.keywords, row.category)}
                />
              </td>
              <td className="px-1" onClick={(event) => event.stopPropagation()}>
                <EditableCell
                  value={row.keywords.join(', ')}
                  placeholder="Нет ключевых слов"
                  twoLines={twoLines}
                  onCommit={(text) => props.onSave(row.filename, row.title, parseKeywords(text), row.category)}
                />
              </td>
              <td className="px-1" onClick={(event) => event.stopPropagation()}>
                <select
                  value={row.category}
                  aria-label="Категория"
                  onChange={(event) => props.onSave(row.filename, row.title, row.keywords, event.target.value)}
                  className={`h-8 w-full rounded border border-transparent bg-transparent px-1 text-sm outline-none hover:border-line-strong focus-visible:ring-2 focus-visible:ring-accent ${
                    row.category ? '' : 'italic text-ink-muted'
                  }`}
                >
                  <option value="">Нет категории</option>
                  {CATEGORY_OPTIONS.map(({ code, name }) => (
                    <option key={code} value={code}>
                      {name}
                    </option>
                  ))}
                </select>
              </td>
              <td className="px-2 text-ink-muted @max-[1000px]:hidden">{resolutionText(dims[row.path])}</td>
              <td className="px-2 text-ink-muted @max-[1000px]:hidden">{formatSize(row.size)}</td>
              <td className="px-1" onClick={(event) => event.stopPropagation()}>
                <StatusMenu status={row.status} onPick={(status) => props.onStatus(row.filename, status)} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
