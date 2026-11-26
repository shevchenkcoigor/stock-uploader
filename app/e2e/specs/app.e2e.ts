import { $, $$, browser, expect } from '@wdio/globals'
import { existsSync, readFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { database, pictureNames, pictures, settingsFile } from '../environment.ts'

const screenshots = resolve(import.meta.dirname, '../artifacts/screenshots')

// Sidebar title and a text only that page shows.
const PAGES = [
  { id: 'scan', nav: '1. Скан', marker: 'Папка с изображениями' },
  { id: 'describe', nav: '2. Описание', marker: 'Метаданные' },
  { id: 'upload', nav: '3. Загрузка', marker: 'Очередь загрузки' },
  { id: 'log', nav: 'Журнал', marker: 'Сообщение' },
  { id: 'settings', nav: 'Настройки', marker: 'Adobe Stock SFTP' },
]

const visiblePage = () => $('main > div:not([hidden])')
const openPage = async (nav: string) => $('nav').$(`button=${nav}`).click()
const screenshot = (name: string) => browser.saveScreenshot(join(screenshots, `${name}.png`))

type WatchedWindow = Window & { e2eErrors?: string[] }

/** Collects what would otherwise only show in the web inspector: uncaught errors and console errors. */
async function watchErrors() {
  await browser.execute(() => {
    const watched = window as WatchedWindow
    if (watched.e2eErrors) return
    const errors: string[] = (watched.e2eErrors = [])
    window.addEventListener('error', (event) => errors.push(String(event.message)))
    window.addEventListener('unhandledrejection', (event) => errors.push(String(event.reason)))
    const original = console.error
    console.error = (...args: unknown[]) => {
      errors.push(args.map(String).join(' '))
      original(...args)
    }
  })
}

const pageErrors = () => browser.execute(() => (window as WatchedWindow).e2eErrors ?? [])

describe('the app window', () => {
  before(async () => {
    // The app has one window. Choosing it ourselves stops the service from asking the app for its windows
    // before element commands: that needs its frontend plugin and otherwise waits 5 s every time.
    await browser.switchToWindow(await browser.getWindowHandle())
    await watchErrors()
  })

  afterEach(async () => {
    expect(await pageErrors()).toEqual([])
  })

  it('starts on the scan page and reads the empty database through Rust', async () => {
    await expect(visiblePage()).toHaveText(expect.stringContaining('Папка с изображениями'))
    // the footer text comes from the `status_summary` command
    await expect($('footer')).toHaveText(expect.stringContaining('База пуста'))
    await screenshot('start')
  })

  it('opens every page', async () => {
    for (const { id, nav, marker } of PAGES) {
      await openPage(nav)
      await expect(visiblePage()).toHaveText(expect.stringContaining(marker))
      await screenshot(`page-${id}`)
    }
  })

  it('scans a folder with two pictures and lists them', async () => {
    await openPage('1. Скан')
    await visiblePage().$('input[placeholder="/путь/к/папке с JPEG"]').setValue(pictures)
    await visiblePage().$('button=Сканировать').click()
    await expect(visiblePage()).toHaveText(expect.stringContaining('Готово · 2 файлов'))
    await expect(visiblePage()).toHaveText(expect.stringContaining('Проблемных файлов нет'))
    await screenshot('scan-done')

    await openPage('2. Описание')
    for (const name of pictureNames) {
      await expect($(`input[aria-label="Отметить ${name}"]`)).toBeExisting()
    }
    await expect($$('main > div:not([hidden]) tbody tr')).toBeElementsArrayOfSize(pictureNames.length)
    await screenshot('describe-two-files')
  })

  it('kept its settings and database inside the temporary folder', () => {
    expect(existsSync(database)).toBe(true)
    expect(JSON.parse(readFileSync(settingsFile, 'utf8')).image_folder).toContain('stock-e2e-')
  })
})
