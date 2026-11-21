interface Props {
  total: number
  pageSize: number
  page: number
  onPage: (page: number) => void
}

const pagesIn = (total: number, pageSize: number) => Math.max(1, Math.ceil(total / pageSize))

/** Page numbers to show: the first, the last and the neighbours of the current one, gaps as null. */
function visiblePages(count: number, current: number): Array<number | null> {
  const wanted = new Set([0, count - 1, current - 1, current, current + 1])
  const pages = [...wanted].filter((page) => page >= 0 && page < count).sort((a, b) => a - b)
  return pages.flatMap((page, index) => (index > 0 && page - pages[index - 1] > 1 ? [null, page] : [page]))
}

export function Pager({ total, pageSize, page, onPage }: Props) {
  const count = pagesIn(total, pageSize)
  if (total === 0) return null
  const from = page * pageSize + 1
  const to = Math.min(total, (page + 1) * pageSize)
  const button = 'h-8 min-w-8 rounded-md px-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-accent'
  return (
    <div className="flex items-center justify-between text-sm text-ink-muted">
      <span>
        {from}–{to} из {total}
      </span>
      {count > 1 && (
        <nav className="flex items-center gap-1" aria-label="Страницы">
          <button type="button" className={`${button} hover:bg-sunken disabled:opacity-40`} disabled={page === 0} onClick={() => onPage(page - 1)}>
            ‹
          </button>
          {visiblePages(count, page).map((number, index) =>
            number === null ? (
              <span key={`gap-${index}`} className="px-1">
                …
              </span>
            ) : (
              <button
                key={number}
                type="button"
                aria-current={number === page ? 'page' : undefined}
                className={`${button} ${number === page ? 'bg-accent-soft font-medium text-ink' : 'text-ink hover:bg-sunken'}`}
                onClick={() => onPage(number)}
              >
                {number + 1}
              </button>
            ),
          )}
          <button type="button" className={`${button} hover:bg-sunken disabled:opacity-40`} disabled={page === count - 1} onClick={() => onPage(page + 1)}>
            ›
          </button>
        </nav>
      )}
    </div>
  )
}
