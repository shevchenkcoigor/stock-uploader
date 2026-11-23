import { useCallback, useEffect, useRef, useState } from 'react'
import { findUpdate, installUpdate, type AvailableUpdate, type DownloadProgress } from './updater'

export type UpdaterState =
  | { kind: 'idle' }
  | { kind: 'checking' }
  | { kind: 'current' }
  | { kind: 'available'; update: AvailableUpdate }
  | { kind: 'installing'; update: AvailableUpdate; progress: DownloadProgress | null }
  | { kind: 'error'; message: string }

const message = (error: unknown) => (error instanceof Error ? error.message : String(error))

/**
 * Update state of the app. `check` is silent about failures when `quiet` (the startup check must never get in the
 * way, e.g. when offline); a manual check reports them.
 */
export function useUpdater(checkOnStart: boolean) {
  const [state, setState] = useState<UpdaterState>({ kind: 'idle' })
  const busy = useRef(false)

  const check = useCallback(async (quiet: boolean) => {
    if (busy.current) return
    busy.current = true
    if (!quiet) setState({ kind: 'checking' })
    try {
      const update = await findUpdate()
      setState(update ? { kind: 'available', update } : { kind: 'current' })
    } catch (error) {
      setState(quiet ? { kind: 'idle' } : { kind: 'error', message: message(error) })
    } finally {
      busy.current = false
    }
  }, [])

  const install = useCallback(async (update: AvailableUpdate) => {
    setState({ kind: 'installing', update, progress: null })
    try {
      await installUpdate(update, (progress) => setState({ kind: 'installing', update, progress }))
    } catch (error) {
      setState({ kind: 'error', message: message(error) })
    }
  }, [])

  useEffect(() => {
    if (!checkOnStart) return
    let cancelled = false
    findUpdate()
      .then((update) => {
        if (!cancelled && update) setState({ kind: 'available', update })
      })
      .catch(() => undefined) // the startup check must never get in the way (offline, no release yet)
    return () => {
      cancelled = true
    }
  }, [checkOnStart])

  return { state, check, install }
}
