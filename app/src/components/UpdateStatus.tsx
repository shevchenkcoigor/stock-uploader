import { progressText } from '../lib/updateText'
import type { AvailableUpdate } from '../lib/updater'
import type { UpdaterState } from '../lib/useUpdater'

export function UpdateBanner({
  state,
  onInstall,
}: {
  state: UpdaterState
  onInstall: (update: AvailableUpdate) => void
}) {
  if (state.kind !== 'available' && state.kind !== 'installing') return null
  const installing = state.kind === 'installing'
  return (
    <div role="status" className="flex items-center gap-3 border-b border-line bg-surface px-4 py-2 text-sm">
      <span className="font-medium">Доступна версия {state.update.version}</span>
      <span className="text-ink-muted">
        {installing ? progressText(state) : 'Приложение перезапустится; идущие операции будут прерваны.'}
      </span>
      {!installing && (
        <button
          type="button"
          onClick={() => onInstall(state.update)}
          className="ml-auto h-8 rounded-md bg-accent px-3 text-sm font-medium text-white focus-visible:ring-2"
        >
          Обновить и перезапустить
        </button>
      )}
    </div>
  )
}
