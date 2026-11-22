const STROKE = 1.8

/** Three rows: the list view. */
export function ListIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth={STROKE} strokeLinecap="round" aria-hidden>
      <path d="M3.5 5.5h13M3.5 10h13M3.5 14.5h13" />
    </svg>
  )
}

/** Four squares: the tiles view. */
export function TilesIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth={STROKE} aria-hidden>
      <rect x="3.5" y="3.5" width="5" height="5" rx="1.2" />
      <rect x="11.5" y="3.5" width="5" height="5" rx="1.2" />
      <rect x="3.5" y="11.5" width="5" height="5" rx="1.2" />
      <rect x="11.5" y="11.5" width="5" height="5" rx="1.2" />
    </svg>
  )
}
