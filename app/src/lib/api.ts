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

export interface ModelChoice {
  id: string
  note: string
}

export interface ModelList {
  models: ModelChoice[]
  selected: string
}

export interface CaptionReport {
  total: number
  captioned: number
  flagged: string[]
  errors: string[]
  newBatches: number
  stopped: boolean
}

export const captionApi = {
  models: () => invoke<ModelList>('caption_models'),
  /** Without names: every new file. With `redo`, files that already have a description are replaced. */
  start: (names: string[] | null, redo: boolean, model: string) =>
    invoke<CaptionReport>('caption_start', { names, redo, model }),
  cancel: () => invoke<void>('caption_cancel'),
}

export interface PendingBatch {
  batchId: number
  folder: string
  part: number
  n: number
}

export interface UploadOverview {
  batches: PendingBatch[]
  counts: Array<{ batchId: number; status: string; n: number }>
}

export interface QueueFile {
  path: string
  filename: string
  status: string
  size: number
  error: string | null
}

export interface UploadReport {
  batch: number
  total: number
  uploaded: number
  failed: string[]
  error: string | null
  stopped: boolean
}

export interface UploadProgress {
  done: number
  total: number
  message: string
  file: string
  sent: number
  size: number
}

export const uploadApi = {
  overview: () => invoke<UploadOverview>('upload_overview'),
  queue: (batch: number) => invoke<QueueFile[]>('upload_queue', { batch }),
  start: (batch: number) => invoke<UploadReport>('upload_start', { batch }),
  pause: () => invoke<void>('upload_pause'),
}

export interface CsvEntry {
  batchId: number
  name: string
  rows: number
  badRows: number
  state: 'built' | 'sent' | 'error'
  path: string
  folder: string
  part: number
  /** [status, files] of batch files not on SFTP yet. */
  waiting: Array<[string, number]>
  /** Epoch seconds; 0 when the file is missing. */
  created: number
  updated: number
}

export interface CsvRow {
  filename: string
  path: string
  title: string
  keywords: string[]
  category: string
  status: string
}

export interface CsvOverview {
  entries: CsvEntry[]
  offered: PendingBatch[]
  counts: Array<{ batchId: number; status: string; n: number }>
}

export interface CsvReport {
  batch: number
  rows: number
  path: string | null
  notes: string | null
  people: number
  problems: string[]
}

export interface SubmittedReport {
  count: number
  warnings: string[]
  waiting: Array<[string, number]>
}

export const csvApi = {
  overview: () => invoke<CsvOverview>('csv_overview'),
  rows: (batch: number) => invoke<CsvRow[]>('csv_batch_rows', { batch }),
  build: (batch: number) => invoke<CsvReport>('csv_build', { batch }),
  markSubmitted: (batch: number) => invoke<SubmittedReport>('csv_mark_submitted', { batch }),
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

export interface FileRow {
  filename: string
  path: string
  size: number
  title: string
  keywords: string[]
  category: string
  status: string
  error: string | null
  updatedAt: string
}

export interface ApproveReport {
  count: number
  newBatches: number
}

export interface BulkResult {
  edited: number | null
  moved: number | null
  newBatches: number
}

export const describeApi = {
  files: () => invoke<FileRow[]>('describe_files'),
  /** False when the file is final (submitted) or not editable. */
  save: (filename: string, title: string, keywords: string[], category: string) =>
    invoke<boolean>('save_metadata', { filename, title, keywords, category }),
  bulk: (names: string[], tag: string, category: string | null, status: string | null) =>
    invoke<BulkResult>('bulk_apply', { names, tag, category, status }),
  setStatus: (names: string[], status: string) => invoke<ApproveReport>('set_files_status', { names, status }),
  approve: (names: string[] | null) => invoke<ApproveReport>('approve_files', { names }),
  imageSizes: (paths: string[]) => invoke<Array<[number, number]>>('image_sizes', { paths }),
}
