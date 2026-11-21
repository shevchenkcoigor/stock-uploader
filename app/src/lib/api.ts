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
