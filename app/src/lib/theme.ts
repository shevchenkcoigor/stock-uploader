import type { ThemeMode } from './api'

const dark = window.matchMedia('(prefers-color-scheme: dark)')
let mode: ThemeMode = 'system'

function paint() {
  const isDark = mode === 'dark' || (mode === 'system' && dark.matches)
  document.documentElement.dataset.theme = isDark ? 'dark' : 'light'
}

dark.addEventListener('change', paint)

/** tokens.css switches colours on [data-theme]; "system" follows the OS setting live. */
export function applyTheme(next: ThemeMode) {
  mode = next
  paint()
}
