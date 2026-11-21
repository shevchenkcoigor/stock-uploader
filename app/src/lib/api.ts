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
