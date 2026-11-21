const NBSP = ' '
const MB_LIMIT = /limit is (\d+) MB/
const MEGAPIXELS = /([\d.]+) MP is outside (\d+)-(\d+) MP/

const baseName = (path: string) => path.split(/[\\/]/).pop() ?? path

/** Short Russian wording for the validation messages; anything unknown (e.g. Claude's) stays as is. */
export function humanizeReason(reason: string): string {
  return reason
    .split('; ')
    .map((part) => {
      const megapixels = MEGAPIXELS.exec(part)
      const limit = MB_LIMIT.exec(part)
      if (part.startsWith('cannot read image')) return 'Файл не открывается'
      if (megapixels) {
        const [, value, low, high] = megapixels
        const shown = String(Number(value)).replace('.', ',')
        return Number(value) < Number(low)
          ? `Меньше минимального размера (${shown} МП, нужно от ${low})`
          : `Больше максимального размера (${shown} МП, не больше ${high})`
      }
      if (part.startsWith('same content as ')) {
        return `Дубликат файла ${baseName(part.slice('same content as '.length))}`
      }
      if (limit) return `Файл больше ${limit[1]} МБ`
      if (part.includes('color profile')) return 'Цветовой профиль не sRGB'
      if (part.includes('is not .jpg') || part.startsWith('content is ')) return 'Не JPEG'
      return part
    })
    .join('; ')
}

/** 1240 -> "1 240" (no-break space). */
export function formatCount(count: number): string {
  return String(count).replace(/\B(?=(\d{3})+(?!\d))/g, NBSP)
}

/** Path relative to the scanned folder's parent (trip_2026/day_03/a.jpg); as is when outside it. */
export function shortPath(path: string, folder: string): string {
  const parent = folder.slice(0, Math.max(folder.lastIndexOf('/'), 0))
  return parent && path.startsWith(`${parent}/`) ? path.slice(parent.length + 1) : path
}

export interface LastScan {
  at: string
  found: number
  counts: { found: number; new: number; known: number; problems: number }
}

export function parseLastScan(raw: string): LastScan | null {
  try {
    const parsed: unknown = JSON.parse(raw)
    if (parsed && typeof parsed === 'object' && 'at' in parsed && 'found' in parsed) return parsed as LastScan
  } catch {
    // an empty or damaged value means "no scan yet"
  }
  return null
}

const pad = (value: number) => String(value).padStart(2, '0')

/** "Последнее сканирование: сегодня, 22:40 · найдено 1 240" for the status line ('' before any scan). */
export function lastScanText(saved: LastScan | null, now: Date): string {
  if (!saved) return ''
  const at = new Date(saved.at)
  const sameDay = at.toDateString() === now.toDateString()
  const day = sameDay ? 'сегодня' : `${pad(at.getDate())}.${pad(at.getMonth() + 1)}.${at.getFullYear()}`
  return `Последнее сканирование: ${day}, ${pad(at.getHours())}:${pad(at.getMinutes())} · найдено ${formatCount(saved.found)}`
}
