import { invoke } from '@tauri-apps/api/core'

export interface EventRow {
  id: number
  at: string
  command: string
  level: 'error' | 'warning' | 'info' | string
  filename: string | null
  message: string
}

export interface EventsPage {
  rows: EventRow[]
  total: number
}

export interface EventFilter {
  text?: string
  level?: string | null
  command?: string | null
  afterId?: number
  limit?: number
}

export interface StatusCount {
  status: string
  n: number
}

export const api = {
  statusSummary: () => invoke<StatusCount[]>('status_summary'),
  searchEvents: (filter: EventFilter) => invoke<EventsPage>('search_events', { filter }),
  eventCommands: () => invoke<string[]>('event_commands'),
}

export type ThemeMode = 'system' | 'light' | 'dark'

export interface Settings {
  dbPath: string
  csvDir: string
  sftpHost: string
  sftpPort: number
  sftpUser: string
  aiTitleLabel: string
  batchSize: number
  captionLimit: number
  captionWorkers: number
  captionPerCall: number
  claudePath: string
  theme: ThemeMode
}

export interface SettingsView {
  settings: Settings
  defaultDbPath: string
  defaultCsvDir: string
}

export const settingsApi = {
  get: () => invoke<SettingsView>('get_settings'),
  save: (settings: Settings, password: string | null) => invoke<SettingsView>('save_settings', { settings, password }),
  checkClaude: () => invoke<string>('check_claude'),
  checkSftp: () => invoke<string>('check_sftp'),
}

export interface ProblemFile {
  path: string
  error: string
  status: string
}

export interface ScanOutcome {
  found: number
  new: number
  known: number
  problems: number
  changedAfterUpload: string[]
  stopped: boolean
}

export interface ScanProgress {
  done: number
  total: number
  message: string
}

export const scanApi = {
  /** Resolves to null when the folder has no JPEG files. */
  start: (folder: string, recursive: boolean) => invoke<ScanOutcome | null>('scan_start', { folder, recursive }),
  cancel: () => invoke<void>('scan_cancel'),
  problemFiles: () => invoke<ProblemFile[]>('problem_files'),
}

export const prefsApi = {
  get: (key: string, fallback: string | null = null) => invoke<string>('get_pref', { key, default: fallback }),
  set: (key: string, value: string) => invoke<void>('set_pref', { key, value }),
}

export const filesApi = {
  thumbnail: (path: string, side: number) => invoke<string | null>('thumbnail', { path, side }),
  open: (path: string) => invoke<void>('open_path', { path }),
  reveal: (path: string) => invoke<void>('reveal_path', { path }),
}
