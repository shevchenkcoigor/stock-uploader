import { useEffect, useState } from 'react'
import { filesApi } from '../lib/api'

export const THUMB_SIDE = 40

function Placeholder() {
  return (
    <svg viewBox="0 0 24 24" className="size-[44%] text-ink-muted" fill="none" stroke="currentColor" strokeWidth="1.5">
      <rect x="3" y="3" width="18" height="18" rx="2.5" />
      <circle cx="16.3" cy="7.7" r="1.7" />
      <path d="M3 19.6 11 11.5l10 8.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

interface Props {
  path: string
  side?: number
  /** Tailwind background class of a dot in the corner (e.g. `bg-warning`). */
  badge?: string
}

/** A framed square preview; a placeholder while loading or when the picture cannot be read.
 *  Give it a `key` of the path: a different picture needs a fresh component. */
export function Thumb({ path, side = THUMB_SIDE, badge }: Props) {
  const [url, setUrl] = useState<string | null>(null)
  useEffect(() => {
    let current = true
    filesApi
      .thumbnail(path, side)
      .then((found) => current && setUrl(found))
      .catch(() => undefined)
    return () => {
      current = false
    }
  }, [path, side])
  return (
    <span
      style={{ width: side, height: side }}
      className="relative flex shrink-0 items-center justify-center overflow-hidden rounded border border-line bg-sunken"
    >
      {url ? <img src={url} alt="" className="size-full object-contain" /> : <Placeholder />}
      {badge && <span aria-hidden className={`absolute right-[3px] top-[3px] size-2.5 rounded-full border-2 border-surface ${badge}`} />}
    </span>
  )
}
