import { useEffect, useState } from 'react'
import { api, prefsApi, type StatusCount } from '../lib/api'
import { SCAN_DONE_EVENT } from '../lib/events'
import { lastScanText, parseLastScan } from '../lib/format'
import { STATUS_ORDER, STATUS_VIEWS } from '../lib/status'
import { SETTINGS_SAVED_EVENT } from '../lib/useSettings'

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
  const [lastScan, setLastScan] = useState('')
  useEffect(() => {
    const load = () => {
      api
        .statusSummary()
        .then((counts) => setText(summaryText(counts)))
        .catch((error) => setText(String(error)))
      void prefsApi.get('last_scan', '{}').then((raw) => setLastScan(lastScanText(parseLastScan(raw), new Date())))
    }
    load()
    // The database may have been switched on the settings page, and a scan changes the counts.
    window.addEventListener(SETTINGS_SAVED_EVENT, load)
    window.addEventListener(SCAN_DONE_EVENT, load)
    return () => {
      window.removeEventListener(SETTINGS_SAVED_EVENT, load)
      window.removeEventListener(SCAN_DONE_EVENT, load)
    }
  }, [])
  return (
    <footer className="flex justify-between border-t border-line bg-window px-4 py-1.5 text-xs text-ink-muted">
      <span>{text}</span>
      <span>{lastScan}</span>
    </footer>
  )
}
