export interface StatusView {
  label: string
  dot: string // Tailwind background class of the status dot
  rank: number // sort order: lower = needs attention sooner
}

// The words and order the statuses are shown in.
export const STATUS_ORDER = ['needs_metadata', 'review', 'pending', 'uploaded', 'submitted', 'invalid'] as const

export const STATUS_VIEWS: Record<string, StatusView> = {
  invalid: { label: 'Ошибка', dot: 'bg-danger', rank: 0 },
  review: { label: 'Проверить', dot: 'bg-warning', rank: 1 },
  needs_metadata: { label: 'Новый', dot: 'bg-ink-muted', rank: 2 },
  pending: { label: 'Описан', dot: 'bg-accent', rank: 3 },
  uploaded: { label: 'На SFTP', dot: 'bg-accent', rank: 4 },
  submitted: { label: 'Отправлен в Adobe', dot: 'bg-success', rank: 5 },
}

const UNKNOWN: StatusView = { label: '—', dot: 'bg-ink-muted', rank: 99 }

export const viewOf = (status: string): StatusView => STATUS_VIEWS[status] ?? UNKNOWN
