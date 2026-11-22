import { describe, expect, test } from 'vitest'
import type { QueueFile } from './api'
import { batchStatusText, errorsWord, progressText, rowsFromQueue, sortedRows, summaryText, totalsOf } from './upload'

const file = (filename: string, status: string, size = 100, error: string | null = null): QueueFile => ({
  path: `/r/${filename}`,
  filename,
  status,
  size,
  error,
})

describe('upload queue', () => {
  const files = [file('a.jpg', 'uploaded'), file('b.jpg', 'pending', 100, 'Permission denied'), file('c.jpg', 'pending'), file('d.jpg', 'pending')]

  test('marks done, failed and queued files while idle', () => {
    expect(rowsFromQueue(files, false, null).map((row) => row.state)).toEqual(['done', 'failed', 'queued', 'queued'])
  })

  test('shows the file on the wire as uploading, even when it failed before', () => {
    const rows = rowsFromQueue(files, true, { file: 'b.jpg', sent: 25, size: 100 })
    expect(rows.map((row) => row.state)).toEqual(['done', 'uploading', 'queued', 'queued'])
    expect(rows[1].progress).toBe(0.25)
  })

  test('before the first progress event the first unfinished file is uploading', () => {
    expect(rowsFromQueue(files, true, null)[1].state).toBe('uploading')
  })

  test('totals and texts', () => {
    const rows = rowsFromQueue(files, true, { file: 'c.jpg', sent: 50, size: 100 })
    const totals = totalsOf(rows)
    expect(totals).toMatchObject({ total: 4, done: 1, failed: 1, sentBytes: 150, totalBytes: 400 })
    expect(summaryText(totals)).toBe('1 из 4 загружено · 1 ошибка')
    expect(progressText(totals)).toMatch(/^38% · /)
  })

  test('sorts by status with problems first, and by name', () => {
    const rows = rowsFromQueue(files, false, null)
    expect(sortedRows(rows, 'status', false).map((row) => row.filename)).toEqual(['b.jpg', 'c.jpg', 'd.jpg', 'a.jpg'])
    expect(sortedRows(rows, 'name', true).map((row) => row.filename)).toEqual(['d.jpg', 'c.jpg', 'b.jpg', 'a.jpg'])
  })

  test('Russian plural of "error"', () => {
    expect([1, 2, 5, 11, 12, 21, 24].map(errorsWord)).toEqual(['ошибка', 'ошибки', 'ошибок', 'ошибок', 'ошибок', 'ошибка', 'ошибки'])
  })

  test('batch status text', () => {
    expect(batchStatusText([])).toBe('—')
    expect(batchStatusText([{ status: 'pending', n: 3 }])).toBe('Описан')
    expect(batchStatusText([{ status: 'uploaded', n: 1 }, { status: 'pending', n: 2 }])).toBe('Описан: 2, На SFTP: 1')
  })
})
