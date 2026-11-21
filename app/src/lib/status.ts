export interface StatusView {
  label: string
  dot: string // Tailwind background class of the status dot
}

// Same words and order as adobe_stock/gui/status.py
export const STATUS_ORDER = ['needs_metadata', 'review', 'pending', 'uploaded', 'submitted', 'invalid'] as const

export const STATUS_VIEWS: Record<string, StatusView> = {
  invalid: { label: 'Ошибка', dot: 'bg-danger' },
  review: { label: 'Проверить', dot: 'bg-warning' },
  needs_metadata: { label: 'Новый', dot: 'bg-ink-muted' },
  pending: { label: 'Описан', dot: 'bg-accent' },
  uploaded: { label: 'На SFTP', dot: 'bg-accent' },
  submitted: { label: 'Отправлен в Adobe', dot: 'bg-success' },
}
