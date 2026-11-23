import { getVersion } from '@tauri-apps/api/app'
import { useEffect, useState } from 'react'
import { updateStatusText } from '../lib/updateText'
import { useUpdater } from '../lib/useUpdater'
import { Button, Section } from './form'

export function UpdateSection() {
  const { state, check, install } = useUpdater(false)
  const [version, setVersion] = useState('')
  useEffect(() => {
    void getVersion().then(setVersion)
  }, [])
  const working = state.kind === 'checking' || state.kind === 'installing'
  return (
    <Section title="Обновления" hint={version ? `Установлена версия ${version}. Новые версии проверяются при запуске.` : undefined}>
      <div className="flex items-center gap-3">
        {state.kind === 'available' ? (
          <Button onClick={() => void install(state.update)}>Обновить и перезапустить</Button>
        ) : (
          <Button onClick={() => void check(false)} disabled={working}>
            Проверить обновления
          </Button>
        )}
        <span aria-live="polite" className={`text-sm ${state.kind === 'error' ? 'text-danger' : 'text-ink-muted'}`}>
          {updateStatusText(state)}
        </span>
      </div>
    </Section>
  )
}
