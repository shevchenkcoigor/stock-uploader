import { $, $$, browser, expect } from '@wdio/globals'
import { createHash } from 'node:crypto'
import { existsSync, readFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { DatabaseSync } from 'node:sqlite'
import { database, described, flagged, pictureNames, pictures, settingsFile, sftpLogin } from '../environment.ts'
import { startSftpServer, type SftpServer } from '../sftp-server.ts'

const screenshots = resolve(import.meta.dirname, '../artifacts/screenshots')
const MIN_TEXT_COLUMN = 100 // px; narrower and a cell shows next to nothing

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
const button = (text: string) => visiblePage().$(`button=${text}`)
const toolbarCaption = () => visiblePage().$('section').$('button=✦ Caption')
const panel = () => visiblePage().$('aside')
const field = (label: string) => visiblePage().$(`.//label[contains(., "${label}")]//input`)
const expectText = (text: string) => expect(visiblePage()).toHaveText(expect.stringContaining(text))
const statuses = () => storedFiles().map((file) => file.status)

/** WebDriver cannot press a native dialog, so its request to Rust is answered inside the window. */
async function agreeToNextDialog() {
  await browser.execute(() => {
    const original = window.fetch
    window.fetch = (input, init) => {
      if (!String(input).includes('dialog%7Cmessage')) return original(input, init)
      window.fetch = original
      const headers = { 'Content-Type': 'application/json', 'Tauri-Response': 'ok' }
      return Promise.resolve(new Response(JSON.stringify('Yes'), { headers }))
    }
  })
}

const pictureHashes = () =>
  pictureNames.map((name) => createHash('sha256').update(readFileSync(join(pictures, name))).digest('hex'))

/** What the app stored, read past the window. */
function storedFiles() {
  const db = new DatabaseSync(database, { readOnly: true })
  try {
    return db
      .prepare('SELECT filename, title, keywords, category, status FROM files ORDER BY filename')
      .all()
      .map((row) => ({
        filename: String(row.filename),
        title: String(row.title),
        category: String(row.category),
        status: String(row.status),
        keywords: JSON.parse(String(row.keywords)) as string[],
      }))
  } finally {
    db.close()
  }
}

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
  const editedTitle = 'A bronze statue near a fountain'
  let originals: string[] = []
  let server: SftpServer

  before(async () => {
    server = await startSftpServer()
    originals = pictureHashes()
    // The app has one window. Choosing it ourselves stops the service from asking the app for its windows
    // before element commands: that needs its frontend plugin and otherwise waits 5 s every time.
    await browser.switchToWindow(await browser.getWindowHandle())
    await watchErrors()
  })

  after(() => server?.close())

  afterEach(async () => {
    expect(await pageErrors()).toEqual([])
  })

  it('starts on the scan page and reads the empty database through Rust', async () => {
    await expect(visiblePage()).toHaveText(expect.stringContaining('Папка с изображениями'))
    // the footer text comes from the `status_summary` command
    await expect($('footer')).toHaveText(expect.stringContaining('База пуста'))
    await screenshot('start')
  })

  it('offers no action while there is nothing to act on', async () => {
    await expect(button('Сканировать')).toBeDisabled()
    await expect(button('Остановить')).toBeDisabled()
    await expect(button('Выбрать папку…')).toBeEnabled()

    await openPage('2. Описание')
    await expect(panel().$('button=✦ Caption')).toBeDisabled()
    await expect(panel().$('button=← Пред')).toBeDisabled()
    await expect(panel().$('button=След →')).toBeDisabled()

    await openPage('3. Загрузка')
    for (const text of ['Повторить ошибки', 'Пауза', 'Загрузить', 'Отметить отправленным']) {
      await expect(button(text)).toBeDisabled()
    }
    await screenshot('upload-empty')
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
    await expect(button('Сканировать')).toBeEnabled()
    await button('Сканировать').click()
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

  it('describes the files with claude and holds the doubtful one for review', async () => {
    await openPage('2. Описание')
    await toolbarCaption().click()
    await expect(visiblePage()).toHaveText(expect.stringContaining('Подписано: 1 из 2 · на проверке: 1'))
    await expect(button('Approve (1)')).toBeEnabled()
    await screenshot('describe-captioned')

    expect(storedFiles()).toEqual([
      {
        filename: pictureNames[0],
        title: `${described.title} Generative AI`,
        keywords: described.keywords,
        category: described.category,
        status: 'pending',
      },
      {
        filename: pictureNames[1],
        title: `${flagged.title} Generative AI`,
        keywords: flagged.keywords,
        category: flagged.category,
        status: 'review',
      },
    ])
  })

  it('keeps an edit and releases the file once it is approved', async () => {
    const name = pictureNames[1]
    // by the name in its row: the stored path is resolved and on macOS differs from `pictures`
    await $(`//tr[.//input[@aria-label="Отметить ${name}"]]//span[text()="${name}"]`).click()
    await expect(panel()).toHaveText(expect.stringContaining(flagged.reason))
    // the title is the first field of the card
    await panel().$('input').setValue(editedTitle)
    await expect(visiblePage()).toHaveText(expect.stringContaining(`Сохранено: ${name}`))

    await panel().$('button=Одобрить файл').click()
    await expect(visiblePage()).toHaveText(expect.stringContaining('Одобрено: 1'))
    await expect(panel().$('button=Одобрить файл')).not.toBeExisting()
    await expect(button('Approve (1)')).not.toBeExisting()
    await screenshot('describe-approved')

    expect(storedFiles()[1]).toMatchObject({ title: editedTitle, keywords: flagged.keywords, status: 'pending' })
  })

  it('offers the upload once files are described, but not «submitted»', async () => {
    await openPage('3. Загрузка')
    await expect(button('Загрузить')).toBeEnabled()
    await expect(button('Отметить отправленным')).toBeDisabled()
    await expect(button('Пауза')).toBeDisabled()
    await screenshot('upload-ready')
  })

  it('points the app at the local SFTP server', async () => {
    await openPage('Настройки')
    await field('Хост').setValue('127.0.0.1')
    await field('Порт').setValue(String(server.port))
    await field('Логин').setValue(sftpLogin.user)
    // nothing may be uploaded before the app has saved these: the default host is Adobe's
    await browser.waitUntil(() => {
      const saved = JSON.parse(readFileSync(settingsFile, 'utf8'))
      return saved.sftp_host === '127.0.0.1' && saved.sftp_port === String(server.port) && saved.sftp_user === sftpLogin.user
    })
  })

  it('can pause a running upload, and not start a second one', async () => {
    server.hold()
    await openPage('3. Загрузка')
    await button('Загрузить').click()
    await browser.waitUntil(() => server.opened.length === 1)
    await expect(button('Пауза')).toBeEnabled()
    for (const text of ['Повторить ошибки', 'Загрузить', 'Отметить отправленным']) {
      await expect(button(text)).toBeDisabled()
    }
    await screenshot('upload-running')

    // the file being sent is finished, the next one is not started
    await button('Пауза').click()
    server.release()
    await expectText('Загружено: 1 из 2, ошибок: 0 · приостановлено')
    expect(server.opened).toHaveLength(1)
    expect(statuses().sort()).toEqual(['pending', 'uploaded'])
    await expect(button('Загрузить')).toBeEnabled()
    await expect(button('Пауза')).toBeDisabled()
    await screenshot('upload-paused')
  })

  it('uploads the rest, as copies that carry the title and keywords', async () => {
    await button('Загрузить').click()
    await expectText('Загружено: 1 из 1, ошибок: 0')
    expect(statuses()).toEqual(['uploaded', 'uploaded'])
    // each file went out once: the one sent before the pause was not sent again
    expect([...server.opened].sort()).toEqual(pictureNames)

    for (const file of storedFiles()) {
      const received = server.files.get(file.filename)!
      expect(received.equals(readFileSync(join(dirname(database), 'export', file.filename)))).toBe(true)
      const text = received.toString('utf8')
      expect(text).toContain(`<rdf:li xml:lang="x-default">${file.title}</rdf:li>`)
      for (const keyword of file.keywords) expect(text).toContain(`<rdf:li>${keyword}</rdf:li>`)
    }
    await expect(button('Загрузить')).toBeDisabled()
    await expect(button('Отметить отправленным')).toBeEnabled()
    await screenshot('upload-done')
  })

  it('marks the uploaded files as submitted', async () => {
    await agreeToNextDialog()
    await button('Отметить отправленным').click()
    await expectText('Отмечено отправленными: 2')
    expect(statuses()).toEqual(['submitted', 'submitted'])
    await expect(button('Отметить отправленным')).toBeDisabled()
    await expect(button('Загрузить')).toBeDisabled()
    await screenshot('upload-submitted')
  })

  it('left the original pictures untouched', () => {
    expect(pictureHashes()).toEqual(originals)
  })

  it('leaves room for the title and the keywords in the table', async () => {
    await openPage('2. Описание')
    for (const size of ['S', 'M', 'L']) {
      await visiblePage().$(`button=${size}`).click()
      const widths = await browser.execute(() =>
        [...document.querySelectorAll('main > div:not([hidden]) thead th')]
          .filter((cell) => /^(Заголовок|Ключевые слова)/.test(cell.textContent ?? ''))
          .map((cell) => cell.getBoundingClientRect().width),
      )
      await screenshot(`describe-columns-${size}`)
      expect(widths).toHaveLength(2)
      expect(Math.min(...widths)).toBeGreaterThanOrEqual(MIN_TEXT_COLUMN)
    }
    await visiblePage().$('button=M').click()
  })

  it('kept its settings and database inside the temporary folder', () => {
    expect(existsSync(database)).toBe(true)
    expect(JSON.parse(readFileSync(settingsFile, 'utf8')).image_folder).toContain('stock-e2e-')
  })
})
