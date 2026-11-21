import { viewOf } from '../lib/status'

export function StatusChip({ status }: { status: string }) {
  const view = viewOf(status)
  return (
    <span className="inline-flex items-center gap-2 text-sm">
      <span aria-hidden className={`size-2.5 rounded-full ${view.dot}`} />
      {view.label}
    </span>
  )
}
