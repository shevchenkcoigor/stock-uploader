import type { ComponentType } from 'react'
import { CsvPage } from './CsvPage'
import { DescribePage } from './DescribePage'
import { LogPage } from './LogPage'
import { ScanPage } from './ScanPage'
import { SettingsPage } from './SettingsPage'
import { UploadPage } from './UploadPage'

export interface PageProps {
  /** True while the page is the visible one (pages stay mounted, so a running task keeps its state). */
  active: boolean
  navigate: (id: string) => void
}

export interface PageDef {
  id: string
  title: string
  component: ComponentType<PageProps>
  bottom?: boolean
}

export const PAGES: PageDef[] = [
  { id: 'scan', title: '1. Скан', component: ScanPage },
  { id: 'describe', title: '2. Описание', component: DescribePage },
  { id: 'csv', title: '3. CSV и отправка', component: CsvPage },
  { id: 'upload', title: '4. Загрузка', component: UploadPage },
  { id: 'log', title: 'Журнал', component: LogPage },
  { id: 'settings', title: 'Настройки', component: SettingsPage, bottom: true },
]
