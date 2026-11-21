import { describe, expect, test } from 'vitest'
import { formatCount, humanizeReason, lastScanText, parseLastScan, shortPath } from './format'

describe('humanizeReason', () => {
  test('translates the validation messages', () => {
    expect(humanizeReason('cannot read image: broken')).toBe('Файл не открывается')
    expect(humanizeReason('45.5 MB, limit is 45 MB')).toBe('Файл больше 45 МБ')
    expect(humanizeReason("color profile is 'Display P3', expected sRGB")).toBe('Цветовой профиль не sRGB')
    expect(humanizeReason("extension '.png' is not .jpg/.jpeg")).toBe('Не JPEG')
    expect(humanizeReason('content is PNG, not JPEG')).toBe('Не JPEG')
  })

  test('tells too small from too large', () => {
    expect(humanizeReason('3.5 MP is outside 4-100 MP')).toBe('Меньше минимального размера (3,5 МП, нужно от 4)')
    expect(humanizeReason('120.0 MP is outside 4-100 MP')).toBe('Больше максимального размера (120 МП, не больше 100)')
  })

  test('names the original of a duplicate and joins several reasons', () => {
    expect(humanizeReason('same content as /a/b/c.jpg; 3.0 MP is outside 4-100 MP')).toBe(
      'Дубликат файла c.jpg; Меньше минимального размера (3 МП, нужно от 4)',
    )
  })

  test('keeps unknown wording as it is', () => {
    expect(humanizeReason('possible trademark')).toBe('possible trademark')
  })
})

describe('formatCount', () => {
  test('groups thousands with a no-break space', () => {
    expect(formatCount(1240)).toBe('1 240')
    expect(formatCount(81)).toBe('81')
    expect(formatCount(1234567)).toBe('1 234 567')
  })
})

describe('shortPath', () => {
  test('is relative to the scanned folder parent', () => {
    expect(shortPath('/photos/trip/day_03/a.jpg', '/photos/trip')).toBe('trip/day_03/a.jpg')
  })
  test('stays whole outside the folder', () => {
    expect(shortPath('/other/a.jpg', '/photos/trip')).toBe('/other/a.jpg')
  })
})

describe('last scan', () => {
  const saved = { at: '2026-11-21T17:42:14', found: 1240, counts: { found: 1240, new: 1, known: 2, problems: 3 } }

  test('says "today" for the same day and the date otherwise', () => {
    expect(lastScanText(saved, new Date('2026-11-21T20:00:00'))).toBe(
      'Последнее сканирование: сегодня, 17:42 · найдено 1 240',
    )
    expect(lastScanText(saved, new Date('2026-11-22T09:00:00'))).toContain('21.11.2026, 17:42')
  })

  test('is empty before any scan and tolerates damaged values', () => {
    expect(lastScanText(null, new Date())).toBe('')
    expect(parseLastScan('{}')).toBeNull()
    expect(parseLastScan('not json')).toBeNull()
    expect(parseLastScan(JSON.stringify(saved))).toEqual(saved)
  })
})
