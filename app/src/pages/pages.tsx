import type { ComponentType } from 'react'
import { LogPage } from './LogPage'
import { Placeholder } from './Placeholder'
import { ScanPage } from './ScanPage'
import { SettingsPage } from './SettingsPage'

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

const todo = (title: string) => () => <Placeholder title={title} />

// Pages move here from the PySide app one at a time (issue #15).
export const PAGES: PageDef[] = [
  { id: 'scan', title: '1. Скан', component: ScanPage },
  { id: 'describe', title: '2. Описание', component: todo('2. Описание') },
  { id: 'upload', title: '3. Загрузка', component: todo('3. Загрузка') },
  { id: 'csv', title: '4. CSV и отправка', component: todo('4. CSV и отправка') },
  { id: 'log', title: 'Журнал', component: LogPage },
  { id: 'settings', title: 'Настройки', component: SettingsPage, bottom: true },
]
