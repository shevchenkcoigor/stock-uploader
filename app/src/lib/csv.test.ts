import { describe, expect, test } from 'vitest'
import type { CsvEntry, CsvRow } from './api'
import { buildText, dateText, plural, refusedText, rowsText, sortEntries, sortRows, waitingText } from './csv'

const entry = (patch: Partial<CsvEntry>): CsvEntry => ({
  batchId: 1,
  name: 'a.csv',
  rows: 3,
  badRows: 0,
  state: 'built',
  path: '/c/a.csv',
  folder: 'trip',
  part: 1,
  waiting: [],
  created: 0,
  updated: 0,
  ...patch,
})
const row = (patch: Partial<CsvRow>): CsvRow => ({ filename: 'a.jpg', path: '/r/a.jpg', title: 't', keywords: ['k'], category: '1', status: 'uploaded', ...patch })

describe('csv page helpers', () => {
  test('plural and rows text', () => {
    expect([1, 2, 5, 11, 21].map((n) => plural(n, 'строка', 'строки', 'строк'))).toEqual(['строка', 'строки', 'строк', 'строк', 'строка'])
    expect(rowsText({ rows: 2, badRows: 1 })).toBe('2 строки · 1 с ошибкой')
    expect(rowsText({ rows: 5, badRows: 0 })).toBe('5 строк')
  })

  test('why a batch cannot be marked sent', () => {
    const waiting: Array<[string, number]> = [['pending', 2]]
    expect(waitingText(entry({ waiting }))).toBe('Нельзя отметить отправленным: батч 1 (trip · 1) загружен не полностью, ещё Описан: 2.')
    expect(waitingText(entry({}))).toBe('')
    expect(refusedText(4, waiting)).toBe('Не отмечено: батч 4 загружен не полностью, ещё Описан: 2.')
  })

  test('dates are dd.mm.yyyy hh:mm, missing is a dash', () => {
    expect(dateText(0)).toBe('—')
    expect(dateText(new Date(2026, 10, 22, 9, 5).getTime() / 1000)).toBe('22.11.2026 09:05')
  })

  test('entries sort by status with problems first and by name', () => {
    const list = [entry({ name: 'b.csv', state: 'sent' }), entry({ name: 'c.csv', state: 'error' }), entry({ name: 'a.csv' })]
    expect(sortEntries(list, 'status', false).map((e) => e.name)).toEqual(['c.csv', 'a.csv', 'b.csv'])
    expect(sortEntries(list, 'name', true).map((e) => e.name)).toEqual(['c.csv', 'b.csv', 'a.csv'])
  })

  test('rows sort with empty titles last', () => {
    const rows = [row({ filename: 'b', title: '' }), row({ filename: 'a', title: 'zebra' }), row({ filename: 'c', title: 'apple' })]
    expect(sortRows(rows, 'title', false).map((r) => r.filename)).toEqual(['c', 'a', 'b'])
  })

  test('build messages', () => {
    const base = { batch: 1, rows: 2, path: '/p', notes: '/n', people: 1, problems: [] }
    expect(buildText({ ...base, rows: 0 })).toMatch(/^В батче нет/)
    expect(buildText({ ...base, problems: ['x', 'y'] })).toBe('ПРОБЛЕМА CSV: x; y')
    expect(buildText(base)).toMatch(/^CSV собран: 2 строк, проверен по правилам Adobe\. .* 1 файлов с людьми\. Дальше в портале/)
  })
})
