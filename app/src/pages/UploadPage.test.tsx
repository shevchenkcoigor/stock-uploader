// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import type { PrepareReport, QueueFile, SubmittedReport, UploadReport } from '../lib/api'
import { UploadPage } from './UploadPage'

// The Tauri boundary: `invoke` (commands), `listen` (events) and the confirmation dialog.
const invoke = vi.hoisted(() => vi.fn())
const ask = vi.hoisted(() => vi.fn())
const listeners = vi.hoisted(() => new Map<string, (event: { payload: unknown }) => void>())

vi.mock('@tauri-apps/api/core', () => ({ invoke }))
vi.mock('@tauri-apps/plugin-dialog', () => ({ ask }))
vi.mock('@tauri-apps/api/event', () => ({
  listen: (name: string, handler: (event: { payload: unknown }) => void) => {
    listeners.set(name, handler)
    return Promise.resolve(() => listeners.delete(name))
  },
}))

const BATCH = 7
const prepared: PrepareReport = { batch: BATCH, total: 2, prepared: 2, current: 0, problems: [], failed: [], stopped: false }
const sent: UploadReport = { batch: BATCH, total: 2, uploaded: 2, failed: [], error: null, blocked: null, stopped: false }

/** An in-memory stand-in for the Rust commands: a queue whose files turn `uploaded` when the batch is sent. */
function fakeBackend(overrides: { prepare?: PrepareReport; start?: UploadReport } = {}) {
  let files: QueueFile[] = ['a.jpg', 'b.jpg'].map((name) => ({ path: `/r/${name}`, filename: name, status: 'pending', size: 2048, error: null }))
  const calls: string[] = []
  const submitted: SubmittedReport = { count: 2, warnings: [], waiting: [] }
  invoke.mockImplementation(async (command: string) => {
    calls.push(command)
    switch (command) {
      case 'upload_overview':
        return {
          batches: [{ batchId: BATCH, folder: '/r', part: 1, n: files.length }],
          counts: [{ batchId: BATCH, status: files[0].status, n: files.length }],
        }
      case 'upload_queue':
        return files
      case 'upload_prepare':
        return overrides.prepare ?? prepared
      case 'upload_start':
        if (!overrides.start || overrides.start.uploaded > 0) {
          files = files.map((file) => ({ ...file, status: 'uploaded' }))
          // like the Rust side: a progress event per finished file, which makes the page re-read the queue
          for (const [index, file] of files.entries()) {
            const message = `uploaded ${file.filename}`
            listeners.get('upload-progress')?.({ payload: { done: index + 1, total: files.length, message, file: file.filename, sent: file.size, size: file.size } })
          }
        }
        return overrides.start ?? sent
      case 'upload_mark_submitted':
        return submitted
      default:
        return null // thumbnails and anything else the page does not depend on
    }
  })
  return { calls: () => calls.filter((name) => name.startsWith('upload_') && !['upload_overview', 'upload_queue'].includes(name)) }
}

const renderPage = () => render(<UploadPage active navigate={() => undefined} />)
const button = (name: string) => screen.getByRole('button', { name })

beforeEach(() => {
  invoke.mockReset()
  ask.mockReset()
  listeners.clear()
})
afterEach(cleanup)

describe('Upload page', () => {
  test('shows the waiting batch and its files, and offers only the upload', async () => {
    fakeBackend()
    renderPage()

    expect(await screen.findByText('a.jpg')).toBeTruthy()
    expect(screen.getByText('b.jpg')).toBeTruthy()
    expect(screen.getByRole('combobox').textContent).toContain(`Батч ${BATCH}`)
    expect((button('Загрузить') as HTMLButtonElement).disabled).toBe(false)
    expect((button('Отметить отправленным') as HTMLButtonElement).disabled).toBe(true)
  })

  test('upload prepares the copies first, then sends them, then allows marking as submitted', async () => {
    const backend = fakeBackend()
    const user = userEvent.setup()
    renderPage()
    await screen.findByText('a.jpg')

    await user.click(button('Загрузить'))

    expect(await screen.findByText('Загружено: 2 из 2, ошибок: 0')).toBeTruthy()
    expect(backend.calls()).toEqual(['upload_prepare', 'upload_start'])
    expect(invoke).toHaveBeenCalledWith('upload_prepare', { batch: BATCH })
    expect(invoke).toHaveBeenCalledWith('upload_start', { batch: BATCH })
    await waitFor(() => expect((button('Отметить отправленным') as HTMLButtonElement).disabled).toBe(false))
    expect((button('Загрузить') as HTMLButtonElement).disabled).toBe(true)
  })

  test('metadata Adobe would reject stops the run before anything is sent', async () => {
    const backend = fakeBackend({ prepare: { ...prepared, prepared: 0, problems: ['a.jpg: empty title'] } })
    const user = userEvent.setup()
    renderPage()
    await screen.findByText('a.jpg')

    await user.click(button('Загрузить'))

    expect(await screen.findByText(/Не подготовлено, исправьте на странице «Описание»: a\.jpg: empty title/)).toBeTruthy()
    expect(backend.calls()).toEqual(['upload_prepare'])
  })

  test('a dropped connection is reported and the files stay in the queue', async () => {
    fakeBackend({ start: { ...sent, uploaded: 0, error: 'cannot upload batch 7: нет соединения' } })
    const user = userEvent.setup()
    renderPage()
    await screen.findByText('a.jpg')

    await user.click(button('Загрузить'))

    expect(await screen.findByText(/Не удалось загрузить: cannot upload batch 7: нет соединения/)).toBeTruthy()
    expect((button('Загрузить') as HTMLButtonElement).disabled).toBe(false)
  })

  test('progress events from the backend update the status line', async () => {
    fakeBackend()
    renderPage()
    await screen.findByText('a.jpg')

    await waitFor(() => expect(listeners.has('upload-progress')).toBe(true))
    listeners.get('upload-progress')?.({ payload: { done: 1, total: 2, message: 'FAILED b.jpg: Permission denied', file: 'b.jpg', sent: 0, size: 2048 } })

    expect(await screen.findByText('FAILED b.jpg: Permission denied')).toBeTruthy()
  })

  test('marking as submitted asks first and does nothing when the answer is no', async () => {
    const backend = fakeBackend()
    const user = userEvent.setup()
    renderPage()
    await screen.findByText('a.jpg')
    await user.click(button('Загрузить'))
    await waitFor(() => expect((button('Отметить отправленным') as HTMLButtonElement).disabled).toBe(false))

    ask.mockResolvedValueOnce(false)
    await user.click(button('Отметить отправленным'))
    expect(ask).toHaveBeenCalledTimes(1)
    expect(backend.calls()).not.toContain('upload_mark_submitted')

    ask.mockResolvedValueOnce(true)
    await user.click(button('Отметить отправленным'))
    expect(await screen.findByText('Отмечено отправленными: 2.')).toBeTruthy()
    expect(invoke).toHaveBeenCalledWith('upload_mark_submitted', { batch: BATCH })
  })
})
