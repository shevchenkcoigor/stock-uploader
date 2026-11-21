import { useEffect, useState } from 'react'
import { api, type StatusCount } from '../lib/api'
import { STATUS_ORDER, STATUS_VIEWS } from '../lib/status'

const EMPTY_TEXT = 'База пуста: начните со скана'

function summaryText(counts: StatusCount[]): string {
  const totals = new Map<string, number>()
  for (const { status, n } of counts) totals.set(status, (totals.get(status) ?? 0) + n)
  const parts = STATUS_ORDER.filter((status) => totals.get(status)).map(
    (status) => `${STATUS_VIEWS[status].label}: ${totals.get(status)}`,
  )
  return parts.length ? parts.join(' · ') : EMPTY_TEXT
}

export function StatusLine() {
  const [text, setText] = useState('')
  useEffect(() => {
    api
      .statusSummary()
      .then((counts) => setText(summaryText(counts)))
      .catch((error) => setText(String(error)))
  }, [])
  return <footer className="border-t border-line bg-window px-4 py-1.5 text-xs text-ink-muted">{text}</footer>
}
