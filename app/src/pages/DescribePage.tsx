import { Pager } from '../components/Pager'
import { Segmented } from '../components/form'
import { filesApi } from '../lib/api'
import { EDITABLE_STATUSES, FILTER_ALL, RECAPTIONABLE, modelTitle } from '../lib/describe'
import { viewOf } from '../lib/status'
import { BulkBar } from './describe/BulkBar'
import { FilesTable } from './describe/FilesTable'
import { MetaPanel } from './describe/MetaPanel'
import { TilesGrid } from './describe/TilesGrid'
import { useCaption } from './describe/useCaption'
import { THUMB_SIDES, useDescribe, type ThumbSize, type ViewMode } from './describe/useDescribe'
import type { PageProps } from './pages'

const VIEWS: ReadonlyArray<{ value: ViewMode; label: string }> = [
  { value: 'list', label: 'Список' },
  { value: 'tiles', label: 'Плитки' },
]
const SIZES: ReadonlyArray<{ value: ThumbSize; label: string }> = [
  { value: 'S', label: 'S' },
  { value: 'M', label: 'M' },
  { value: 'L', label: 'L' },
]

export function DescribePage({ active }: PageProps) {
  const state = useDescribe(active)
  const caption = useCaption({ say: state.say })
  const { visible, pageRows, marked, markedCount, selected, selectedRow, actions } = state
  const [thumbRow, thumbTile] = THUMB_SIDES[state.thumbSize]
  const index = visible.findIndex((row) => row.filename === selected)
  const allState = markedCount === 0 ? 'none' : markedCount === visible.length ? 'all' : 'some'
  const runCaption = () => {
    // nothing marked: every new file; something marked: only the marked ones that may be captioned
    const targets =
      markedCount > 0
        ? state.rows.filter((row) => marked.has(row.filename) && RECAPTIONABLE.includes(row.status))
        : null
    const described = targets?.filter((row) => row.status !== 'needs_metadata').length ?? 0
    void caption.start(targets ? targets.map((row) => row.filename) : null, targets !== null, described)
  }
  const open = (path: string) => void filesApi.open(path).catch(() => undefined)

  return (
    <div className="flex h-full gap-4 p-6">
      <section className="flex min-w-0 flex-1 flex-col gap-3 rounded-lg border border-line bg-surface p-3">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="mr-1 text-base font-semibold">Файлы</h2>
          <select
            value={caption.model}
            aria-label="Модель Claude"
            disabled={caption.running}
            onChange={(event) => caption.setModel(event.target.value)}
            className="h-8 rounded-md border border-line-strong bg-surface px-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:opacity-50"
          >
            {caption.models.map(({ id, note }) => (
              <option key={id} value={id} title={`${id} — ${note}`}>
                {modelTitle(id)}
              </option>
            ))}
          </select>
          {caption.running ? (
            <button
              type="button"
              onClick={caption.cancel}
              className="h-8 rounded-md border border-line-strong bg-surface px-3 text-sm outline-none hover:bg-sunken focus-visible:ring-2 focus-visible:ring-accent"
            >
              Отмена
            </button>
          ) : (
            <button
              type="button"
              disabled={!caption.model}
              title={markedCount > 0 ? 'Подписать отмеченные файлы' : 'Подписать все новые файлы'}
              onClick={runCaption}
              className="h-8 rounded-md bg-accent px-3 text-sm font-medium text-white outline-none hover:opacity-90 focus-visible:ring-2 focus-visible:ring-accent disabled:opacity-50"
            >
              ✦ Caption
            </button>
          )}
          {state.reviewCount > 0 && (
            <button
              type="button"
              title="Одобрить все файлы со статусом «Проверить»"
              onClick={() => void actions.approve(null)}
              className="h-8 rounded-md border border-line-strong bg-surface px-3 text-sm outline-none hover:bg-sunken focus-visible:ring-2 focus-visible:ring-accent"
            >
              Approve ({state.reviewCount})
            </button>
          )}
          <span className="flex-1" />
          <select
            value={state.filter}
            aria-label="Фильтр по статусу"
            onChange={(event) => state.changeFilter(event.target.value)}
            className="h-8 rounded-md border border-line-strong bg-surface px-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-accent"
          >
            <option value={FILTER_ALL}>Все статусы</option>
            {EDITABLE_STATUSES.map((key) => (
              <option key={key} value={key}>
                {viewOf(key).label}
              </option>
            ))}
          </select>
          <Segmented value={state.thumbSize} options={SIZES} onChange={state.setThumbSize} />
          <Segmented value={state.view} options={VIEWS} onChange={state.changeView} />
        </div>

        <BulkBar
          marked={markedCount}
          total={visible.length}
          showSelectAll={state.view === 'tiles'}
          onToggleAll={state.toggleAll}
          onApply={(tag, category, status) => void actions.bulk(tag, category, status)}
        />

        <div className="min-h-0 flex-1 overflow-auto">
          {state.view === 'list' ? (
            <FilesTable
              rows={pageRows}
              selected={selected}
              marked={marked}
              allState={allState}
              dims={state.dims}
              thumbSide={thumbRow}
              sort={state.sort}
              onSort={state.sortBy}
              onSelect={state.select}
              onToggleMark={state.toggleMark}
              onToggleAll={state.toggleAll}
              onSave={(...args) => void actions.save(...args)}
              onStatus={(filename, status) => void actions.moveFiles([filename], status)}
              onOpen={open}
            />
          ) : (
            <TilesGrid
              rows={pageRows}
              selected={selected}
              marked={marked}
              tileSide={thumbTile}
              onSelect={state.select}
              onToggleMark={state.toggleMark}
              onOpen={open}
            />
          )}
          {visible.length === 0 && (
            <p className="p-8 text-center text-sm text-ink-muted">Нет файлов для описания: сначала сканирование</p>
          )}
        </div>

        {caption.running && (
          <div className="flex items-center gap-3">
            <div className="h-2 flex-1 overflow-hidden rounded-full bg-sunken" role="progressbar" aria-valuenow={caption.percent ?? undefined}>
              <div className="h-full bg-accent transition-[width]" style={{ width: `${caption.percent ?? 0}%` }} />
            </div>
            <span className="text-xs text-ink-muted">{caption.progress?.message ?? 'Подписываю…'}</span>
          </div>
        )}
        <Pager total={visible.length} pageSize={state.pageSize} page={state.page} onPage={state.setPage} />
        <p aria-live="polite" className={`h-4 truncate text-xs ${state.notice?.error ? 'text-danger' : 'text-ink-muted'}`}>
          {state.notice?.text}
        </p>
      </section>

      <MetaPanel
        key={selectedRow?.filename ?? ''}
        row={selectedRow}
        dims={state.dims}
        hasPrev={index > 0}
        hasNext={index >= 0 && index < visible.length - 1}
        onSave={(...args) => void actions.save(...args)}
        onStatus={(filename, status) => void actions.moveFiles([filename], status)}
        onApprove={(filename) => void actions.approve([filename])}
        onStep={state.step}
      />
    </div>
  )
}
