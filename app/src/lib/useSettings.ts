import { useCallback, useEffect, useRef, useState } from 'react'
import { settingsApi, type Settings, type SettingsView } from './api'
import { applyTheme } from './theme'

const AUTOSAVE_DELAY_MS = 600
export const SETTINGS_SAVED_EVENT = 'settings-saved'

export type SaveState = 'idle' | 'saving' | 'saved' | 'error'

export interface SettingsModel {
  view: SettingsView | null
  draft: Settings | null
  password: string
  state: SaveState
  error: string | null
  change: (patch: Partial<Settings>) => void
  setPassword: (value: string) => void
  /** Saves right now (before a check); resolves to false when the values are invalid. */
  flush: () => Promise<boolean>
}

export function useSettings(): SettingsModel {
  const [view, setView] = useState<SettingsView | null>(null)
  const [draft, setDraft] = useState<Settings | null>(null)
  const [password, setPassword] = useState('')
  const [state, setState] = useState<SaveState>('idle')
  const [error, setError] = useState<string | null>(null)
  const [dirty, setDirty] = useState(false)
  const latest = useRef({ draft, password })
  const edits = useRef(0) // counts changes, to tell whether anything was typed while a save was running
  useEffect(() => {
    latest.current = { draft, password }
  })

  useEffect(() => {
    settingsApi
      .get()
      .then((loaded) => {
        setView(loaded)
        setDraft(loaded.settings)
        applyTheme(loaded.settings.theme)
      })
      .catch((reason) => {
        setState('error')
        setError(String(reason))
      })
  }, [])

  const flush = useCallback(async () => {
    const { draft: current, password: typed } = latest.current
    if (!current) return false
    const editsAtStart = edits.current
    setState('saving')
    try {
      const saved = await settingsApi.save(current, typed || null)
      setView(saved)
      if (edits.current === editsAtStart) {
        setPassword('')
        setDirty(false)
      }
      setError(null)
      setState('saved')
      applyTheme(saved.settings.theme)
      window.dispatchEvent(new Event(SETTINGS_SAVED_EVENT))
      return true
    } catch (reason) {
      setState('error')
      setError(String(reason))
      return false
    }
  }, [])

  useEffect(() => {
    if (!dirty) return
    const timer = window.setTimeout(() => void flush(), AUTOSAVE_DELAY_MS)
    return () => window.clearTimeout(timer)
  }, [dirty, draft, password, flush])

  const change = useCallback((patch: Partial<Settings>) => {
    edits.current += 1
    setDraft((current) => (current ? { ...current, ...patch } : current))
    setDirty(true)
  }, [])

  const changePassword = useCallback((value: string) => {
    edits.current += 1
    setPassword(value)
    setDirty(true)
  }, [])

  return { view, draft, password, state, error, change, setPassword: changePassword, flush }
}
