export const pagesIn = (total: number, pageSize: number) => Math.max(1, Math.ceil(total / pageSize))

const MAX_BUTTONS = 7 // more pages than this are shortened with an ellipsis

/** Page numbers (0-based) to show as buttons; null is an ellipsis. First and last always stay. */
export function visiblePages(count: number, current: number): Array<number | null> {
  if (count <= MAX_BUTTONS) return Array.from({ length: count }, (_, index) => index)
  const wanted = new Set([0, count - 1, current - 1, current, current + 1])
  const pages = [...wanted].filter((page) => page >= 0 && page < count).sort((a, b) => a - b)
  return pages.flatMap((page, index) => (index > 0 && page - pages[index - 1] > 1 ? [null, page] : [page]))
}
