import { describe, expect, test } from 'vitest'
import type { FileRow } from './api'
import { categoryName } from './categories'
import {
  captionSummary,
  filterRows,
  formatSize,
  modelTitle,
  movedText,
  parseKeywords,
  resolutionText,
  sortRows,
} from './describe'
import { pagesIn, visiblePages } from './paging'

const file = (name: string, patch: Partial<FileRow> = {}): FileRow => ({
  filename: name,
  path: `/r/${name}`,
  size: 1,
  title: '',
  keywords: [],
  category: '',
  status: 'needs_metadata',
  error: null,
  updatedAt: '',
  ...patch,
})
const names = (rows: FileRow[]) => rows.map((row) => row.filename)

describe('parseKeywords', () => {
  test('trims, drops empty and repeated tags and keeps the order', () => {
    expect(parseKeywords('a, b,, a')).toEqual(['a', 'b'])
    expect(parseKeywords('x\ny')).toEqual(['x', 'y'])
    expect(parseKeywords('  ')).toEqual([])
  })
})

describe('formatting', () => {
  test('size in megabytes with a decimal comma', () => {
    expect(formatSize(3 * 1024 * 1024 + 512 * 1024)).toBe('3,5 МБ')
  })
  test('resolution is empty when unknown', () => {
    expect(resolutionText([6000, 4000])).toBe('6000 × 4000')
    expect(resolutionText([0, 0])).toBe('')
    expect(resolutionText(undefined)).toBe('')
  })
  test('category names, empty for none or an unknown code', () => {
    expect(categoryName('1')).toBe('Animals')
    expect(categoryName('')).toBe('')
    expect(categoryName('99')).toBe('')
  })
})

describe('sortRows', () => {
  const rows = [
    file('b.jpg', { title: 'Beta', status: 'pending', size: 5 }),
    file('a.jpg', { title: '', status: 'review', size: 9 }),
    file('c.jpg', { title: 'alpha', status: 'invalid', size: 1 }),
  ]
  test('by name, ascending and descending', () => {
    expect(names(sortRows(rows, 'name', false))).toEqual(['a.jpg', 'b.jpg', 'c.jpg'])
    expect(names(sortRows(rows, 'name', true))).toEqual(['c.jpg', 'b.jpg', 'a.jpg'])
  })
  test('empty titles go last, ignoring case', () => {
    expect(names(sortRows(rows, 'title', false))).toEqual(['c.jpg', 'b.jpg', 'a.jpg'])
  })
  test('by status puts the ones that need attention first', () => {
    expect(names(sortRows(rows, 'status', false))).toEqual(['c.jpg', 'a.jpg', 'b.jpg'])
  })
  test('by size and by resolution (unreadable first)', () => {
    expect(names(sortRows(rows, 'size', false))).toEqual(['c.jpg', 'b.jpg', 'a.jpg'])
    const sizes = { '/r/a.jpg': [10, 10], '/r/b.jpg': [0, 0], '/r/c.jpg': [20, 20] } as const
    expect(names(sortRows(rows, 'resolution', false, sizes))).toEqual(['b.jpg', 'a.jpg', 'c.jpg'])
  })
  test('does not change the input and keeps the order of equal rows', () => {
    const same = [file('x.jpg', { size: 1 }), file('y.jpg', { size: 1 })]
    expect(names(sortRows(same, 'size', true))).toEqual(['x.jpg', 'y.jpg'])
    expect(names(rows)).toEqual(['b.jpg', 'a.jpg', 'c.jpg'])
  })
})

describe('filterRows', () => {
  test('keeps one status or everything', () => {
    const rows = [file('a.jpg'), file('b.jpg', { status: 'review' })]
    expect(names(filterRows(rows, 'review'))).toEqual(['b.jpg'])
    expect(filterRows(rows, 'all')).toHaveLength(2)
  })
})

describe('movedText', () => {
  const rows = [file('a.jpg'), file('b.jpg', { title: 'T', keywords: ['k'] })]
  test('mentions files that cannot leave "new" without text', () => {
    expect(movedText(1, ['a.jpg', 'b.jpg'], 'review', rows)).toBe(
      'Статус сменён: 1 из 2 (без заголовка или ключевых слов: 1)',
    )
  })
  test('says nothing extra when moving back to "new"', () => {
    expect(movedText(1, ['a.jpg'], 'needs_metadata', rows)).toBe('Статус сменён: 1 из 1')
  })
})

describe('paging', () => {
  test('counts pages, at least one', () => {
    expect(pagesIn(0, 20)).toBe(1)
    expect(pagesIn(41, 20)).toBe(3)
  })
  test('shortens long page lists with an ellipsis', () => {
    expect(visiblePages(5, 0)).toEqual([0, 1, 2, 3, 4])
    expect(visiblePages(20, 10)).toEqual([0, null, 9, 10, 11, null, 19])
    expect(visiblePages(20, 0)).toEqual([0, 1, null, 19])
  })
})

describe('caption helpers', () => {
  test('shortens model ids into names', () => {
    expect(modelTitle('claude-sonnet-5-5')).toBe('Sonnet 5.5')
    expect(modelTitle('claude-fable-5-2')).toBe('Fable 5.2')
  })

  test('summarises a run', () => {
    const base = { total: 10, captioned: 8, flagged: ['a: x', 'b: y'], stopped: false }
    expect(captionSummary(base)).toBe('Подписано: 8 из 10 · на проверке: 2')
    expect(captionSummary({ ...base, stopped: true })).toMatch(/отменено$/)
    expect(captionSummary({ ...base, total: 0 })).toMatch(/^Нечего подписывать/)
  })
})
