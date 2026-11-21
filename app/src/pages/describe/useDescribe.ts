import { useCallback, useEffect, useMemo, useState } from 'react'
import { describeApi, prefsApi, type FileRow } from '../../lib/api'
import {
  FILTER_ALL,
  filterRows,
  movedText,
  sortRows,
  type Dimensions,
  type SortField,
} from '../../lib/describe'
import { DATA_CHANGED_EVENT } from '../../lib/events'
import { pagesIn } from '../../lib/paging'
import { SETTINGS_SAVED_EVENT } from '../../lib/useSettings'

export type ViewMode = 'list' | 'tiles'
export type ThumbSize = 'S' | 'M' | 'L'
export interface Notice {
  text: string
  error: boolean
}
export interface Modifiers {
  meta: boolean
  shift: boolean
}

export const PAGE_SIZES: Record<ViewMode, number> = { list: 20, tiles: 60 }
// (list row, tile) picture side in pixels
export const THUMB_SIDES: Record<ThumbSize, readonly [number, number]> = { S: [24, 80], M: [40, 120], L: [72, 180] }
const THUMB_SIZE_KEY = 'thumb_size'
const isThumbSize = (value: string): value is ThumbSize => value in THUMB_SIDES

const changed = () => window.dispatchEvent(new Event(DATA_CHANGED_EVENT))

function toggled(set: ReadonlySet<string>, value: string): ReadonlySet<string> {
  const next = new Set(set)
  if (!next.delete(value)) next.add(value)
  return next
}

export function useDescribe(active: boolean) {
  const [rows, setRows] = useState<FileRow[]>([])
  const [filter, setFilter] = useState(FILTER_ALL)
  const [sort, setSort] = useState<{ field: SortField; descending: boolean }>({ field: 'status', descending: false })
  const [view, setView] = useState<ViewMode>('list')
  const [thumbSize, setThumbSizeState] = useState<ThumbSize>('M')
  const [selected, setSelected] = useState('')
  const [marked, setMarked] = useState<ReadonlySet<string>>(new Set())
  const [page, setPage] = useState(0)
  const [dims, setDims] = useState<Dimensions>({})
  const [notice, setNotice] = useState<Notice | null>(null)

  const say = useCallback((text: string, error = false) => setNotice({ text, error }), [])

  const refresh = useCallback(() => {
    describeApi
      .files()
      .then(setRows)
      .catch((reason) => say(String(reason), true))
  }, [say])

  useEffect(() => {
    refresh()
    void prefsApi.get(THUMB_SIZE_KEY, 'M').then((saved) => isThumbSize(saved) && setThumbSizeState(saved))
    window.addEventListener(DATA_CHANGED_EVENT, refresh)
    window.addEventListener(SETTINGS_SAVED_EVENT, refresh)
    return () => {
      window.removeEventListener(DATA_CHANGED_EVENT, refresh)
      window.removeEventListener(SETTINGS_SAVED_EVENT, refresh)
    }
  }, [refresh])

  useEffect(() => {
    if (active) refresh()
  }, [active, refresh])

  const visible = useMemo(
    () => sortRows(filterRows(rows, filter), sort.field, sort.descending, dims),
    [rows, filter, sort, dims],
  )
  const pageSize = PAGE_SIZES[view]
  const pageCount = pagesIn(visible.length, pageSize)
  const shownPage = Math.min(page, pageCount - 1)
  const pageRows = useMemo(
    () => visible.slice(shownPage * pageSize, (shownPage + 1) * pageSize),
    [visible, shownPage, pageSize],
  )
  const selectedRow = rows.find((row) => row.filename === selected) ?? null

  // Pixel sizes come from the file headers: for what is on screen, and for everything when sorting by them.
  useEffect(() => {
    const wanted = (sort.field === 'resolution' ? visible : pageRows).map((row) => row.path)
    if (selectedRow) wanted.push(selectedRow.path)
    const missing = [...new Set(wanted)].filter((path) => !(path in dims))
    if (missing.length === 0) return
    void describeApi.imageSizes(missing).then((sizes) =>
      setDims((known) => ({ ...known, ...Object.fromEntries(missing.map((path, index) => [path, sizes[index]])) })),
    )
  }, [visible, pageRows, sort.field, selectedRow, dims])

  const sortBy = (field: SortField) => {
    setSort((current) => ({ field, descending: current.field === field && !current.descending }))
    setPage(0)
  }
  const changeFilter = (next: string) => {
    setFilter(next)
    setPage(0)
  }
  const changeView = (next: ViewMode) => {
    setView(next)
    setPage(0)
  }
  const setThumbSize = (next: ThumbSize) => {
    setThumbSizeState(next)
    void prefsApi.set(THUMB_SIZE_KEY, next)
  }

  const names = useMemo(() => visible.map((row) => row.filename), [visible])

  const select = (filename: string, { meta, shift }: Modifiers = { meta: false, shift: false }) => {
    if (shift && selected) {
      const [from, to] = [names.indexOf(selected), names.indexOf(filename)].sort((a, b) => a - b)
      if (from >= 0) setMarked(new Set(names.slice(from, to + 1))) // a range marks every file in it
    } else if (meta) {
      setMarked((current) => toggled(current, filename))
    }
    setSelected(filename)
  }
  const step = (delta: number) => {
    if (names.length === 0) return
    const position = selected && names.includes(selected) ? names.indexOf(selected) + delta : 0
    const index = Math.max(0, Math.min(names.length - 1, position))
    setSelected(names[index])
    setPage(Math.floor(index / pageSize))
  }
  const toggleMark = (filename: string) => setMarked((current) => toggled(current, filename))
  const markedNames = names.filter((name) => marked.has(name))
  const toggleAll = () => setMarked(markedNames.length === names.length ? new Set() : new Set(names))

  const report = async (work: () => Promise<string>, failure: string) => {
    try {
      say(await work())
    } catch (reason) {
      say(`ОШИБКА: ${failure}: ${reason}`, true)
    } finally {
      changed()
    }
  }

  const save = (filename: string, title: string, keywords: string[], category: string) =>
    report(async () => {
      const saved = await describeApi.save(filename, title, keywords, category)
      if (!saved) throw new Error('файл уже отправлен или не подлежит описанию, его не правят')
      return `Сохранено: ${filename}`
    }, `не удалось сохранить ${filename}`)

  const moveFiles = (files: string[], status: string) =>
    report(async () => {
      const result = await describeApi.setStatus(files, status)
      return movedText(result.count, files, status, rows)
    }, 'не удалось сменить статус')

  const approve = (files: string[] | null) =>
    report(async () => `Одобрено: ${(await describeApi.approve(files)).count}`, 'не удалось одобрить')

  const bulk = (tag: string, category: string | null, status: string | null) =>
    report(async () => {
      const result = await describeApi.bulk(markedNames, tag, category, status)
      const parts = []
      if (result.edited !== null) parts.push(`Изменено файлов: ${result.edited} из ${markedNames.length}`)
      if (result.moved !== null && status) parts.push(movedText(result.moved, markedNames, status, rows))
      return parts.join('. ')
    }, 'не удалось изменить файлы')

  return {
    rows,
    visible,
    pageRows,
    page: shownPage,
    pageSize,
    setPage,
    filter,
    changeFilter,
    sort,
    sortBy,
    view,
    changeView,
    thumbSize,
    setThumbSize,
    selected,
    selectedRow,
    select,
    step,
    marked,
    markedNames,
    markedCount: markedNames.length,
    toggleMark,
    toggleAll,
    dims,
    notice,
    say,
    reviewCount: rows.filter((row) => row.status === 'review').length,
    actions: { save, moveFiles, approve, bulk },
  }
}
