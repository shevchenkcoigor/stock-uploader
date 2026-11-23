import type { UpdaterState } from './useUpdater'

const MEGABYTE = 1024 * 1024
const PERCENT = 100

export function progressText(state: Extract<UpdaterState, { kind: 'installing' }>): string {
  const { progress } = state
  if (!progress) return 'Подготовка…'
  if (progress.total) return `Загрузка: ${Math.min(PERCENT, Math.round((progress.downloaded / progress.total) * PERCENT))}%`
  return `Загружено ${(progress.downloaded / MEGABYTE).toFixed(1)} МБ`
}

export function updateStatusText(state: UpdaterState): string {
  switch (state.kind) {
    case 'checking':
      return 'Проверяю…'
    case 'current':
      return 'Установлена последняя версия'
    case 'available':
      return `Доступна версия ${state.update.version}`
    case 'installing':
      return progressText(state)
    case 'error':
      return `Не удалось проверить обновления: ${state.message}`
    default:
      return ''
  }
}
