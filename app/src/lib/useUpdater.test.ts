// @vitest-environment jsdom
import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const invoke = vi.hoisted(() => vi.fn())
const relaunch = vi.hoisted(() => vi.fn())
const check = vi.hoisted(() => vi.fn())

vi.mock('@tauri-apps/api/core', () => ({ invoke }))
vi.mock('@tauri-apps/plugin-process', () => ({ relaunch }))
vi.mock('@tauri-apps/plugin-updater', () => ({ check }))

import type { AvailableUpdate } from './updater'
import { useUpdater, type UpdaterState } from './useUpdater'

const availableUpdate = (state: UpdaterState): AvailableUpdate => {
  if (state.kind !== 'available') throw new Error(`expected an available update, got ${state.kind}`)
  return state.update
}

const fakeUpdate = (downloadAndInstall = vi.fn().mockResolvedValue(undefined)) => ({
  version: '0.2.0',
  body: 'notes',
  downloadAndInstall,
})

beforeEach(() => {
  vi.resetAllMocks()
  invoke.mockResolvedValue(undefined)
})

describe('useUpdater', () => {
  it('reports an available update on start', async () => {
    check.mockResolvedValue(fakeUpdate())
    const { result } = renderHook(() => useUpdater(true))
    await waitFor(() => expect(result.current.state.kind).toBe('available'))
  })

  it('stays silent when the startup check fails', async () => {
    check.mockRejectedValue(new Error('offline'))
    const { result } = renderHook(() => useUpdater(true))
    await waitFor(() => expect(check).toHaveBeenCalled())
    await waitFor(() => expect(result.current.state.kind).toBe('idle'))
  })

  it('reports a failed manual check', async () => {
    check.mockRejectedValue(new Error('offline'))
    const { result } = renderHook(() => useUpdater(false))
    await act(() => result.current.check(false))
    expect(result.current.state).toEqual({ kind: 'error', message: 'offline' })
  })

  it('says the app is current when there is no update', async () => {
    check.mockResolvedValue(null)
    const { result } = renderHook(() => useUpdater(false))
    await act(() => result.current.check(false))
    expect(result.current.state.kind).toBe('current')
  })

  it('backs up the database before installing, then restarts', async () => {
    const downloadAndInstall = vi.fn().mockResolvedValue(undefined)
    check.mockResolvedValue(fakeUpdate(downloadAndInstall))
    const { result } = renderHook(() => useUpdater(false))
    await act(() => result.current.check(false))
    await act(() => result.current.install(availableUpdate(result.current.state)))
    expect(invoke).toHaveBeenCalledWith('backup_before_update')
    expect(invoke.mock.invocationCallOrder[0]).toBeLessThan(downloadAndInstall.mock.invocationCallOrder[0])
    expect(relaunch).toHaveBeenCalled()
  })

  it('does not install when the backup fails', async () => {
    const downloadAndInstall = vi.fn()
    check.mockResolvedValue(fakeUpdate(downloadAndInstall))
    invoke.mockRejectedValue('backup failed')
    const { result } = renderHook(() => useUpdater(false))
    await act(() => result.current.check(false))
    await act(() => result.current.install(availableUpdate(result.current.state)))
    expect(downloadAndInstall).not.toHaveBeenCalled()
    expect(result.current.state.kind).toBe('error')
  })
})
