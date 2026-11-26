// The app's settings and database live under a new temporary HOME, so a local run touches neither the
// developer's settings nor their database. The launcher creates it and the workers inherit the path. Nothing
// here removes files: the folder stays in the system temp directory, which the OS cleans.
import { execFileSync } from 'node:child_process'
import { mkdirSync, mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { encode } from 'jpeg-js'

export const home = (process.env.STOCK_E2E_HOME ??= mkdtempSync(join(tmpdir(), 'stock-e2e-')))
export const pictures = join(home, 'pictures')
export const database = join(home, 'db', 'state.db')
export const pictureNames = ['first.jpg', 'second.jpg']

const windows = process.platform === 'win32'
const dataDirs: Partial<Record<NodeJS.Platform, string>> = {
  darwin: join(home, 'Library', 'Application Support'),
  win32: join(home, 'data'),
}
const dataDir = dataDirs[process.platform] ?? join(home, '.local', 'share')
export const settingsFile = join(dataDir, 'com.stock-uploader.desktop', 'settings.json')

// Stands in for `claude`: no scenario may reach the real one.
const fakeClaude = join(home, windows ? 'fake-claude.exe' : 'fake-claude')
const claudeAnswer = join(home, 'claude-answer.json')

// The app sends its own previews, named img001.jpg and on, in the order the files were scanned.
export const described = {
  title: 'A red fox sits in fresh snow',
  keywords: ['fox', 'snow', 'winter'],
  category: '1',
}
export const flagged = {
  title: 'A bronze statue in a city park',
  keywords: ['statue', 'park'],
  category: '2',
  reason: 'resembles a real person',
}

export const appEnv = {
  HOME: home,
  XDG_DATA_HOME: join(home, '.local', 'share'),
  XDG_CONFIG_HOME: join(home, '.config'),
  XDG_CACHE_HOME: join(home, '.cache'),
  STOCK_STATE_DB: database,
  STOCK_CLAUDE_PATH: fakeClaude,
  // read only by Windows builds' stand-in for HOME; see `data_root` in prefs.rs
  ...(windows ? { STOCK_E2E_DATA_DIR: dataDir } : {}),
}

// Adobe Stock takes pictures of 4 megapixels and more, so smaller ones would be listed as problems.
const WIDTH = 2400
const HEIGHT = 1800
const BYTES_PER_PIXEL = 4

/** Two different JPEGs: identical files would count as duplicates of each other. */
export function writePictures(): void {
  mkdirSync(pictures, { recursive: true })
  pictureNames.forEach((name, index) => {
    const data = Buffer.alloc(WIDTH * HEIGHT * BYTES_PER_PIXEL)
    for (let pixel = 0; pixel < WIDTH * HEIGHT; pixel += 1) {
      const at = pixel * BYTES_PER_PIXEL
      data[at] = (pixel % WIDTH) % 256
      data[at + 1] = Math.floor(pixel / WIDTH) % 256
      data[at + 2] = index * 200
      data[at + 3] = 255
    }
    writeFileSync(join(pictures, name), encode({ data, width: WIDTH, height: HEIGHT }, 90).data)
  })
}

/** A program that answers every call with the same description of two pictures; the second one needs a person. */
export function writeFakeClaude(): void {
  const item = (file: string, source: typeof described, reason: string) => ({
    file,
    title: source.title,
    keywords: source.keywords,
    category: Number(source.category),
    people_visible: false,
    content_type: 'photo',
    review_reason: reason,
  })
  const items = [item('img001.jpg', described, ''), item('img002.jpg', flagged, flagged.reason)]
  writeFileSync(claudeAnswer, JSON.stringify({ structured_output: { items } }))
  if (windows) compileFakeClaude()
  else writeFileSync(fakeClaude, `#!/bin/sh\ncat '${claudeAnswer}'\n`, { mode: 0o755 })
}

// A batch file would not do: Rust refuses to pass it the multi-line prompt, so Windows gets a real program.
function compileFakeClaude(): void {
  const source = join(home, 'fake-claude.rs')
  writeFileSync(source, `fn main() { print!("{}", include_str!(r"${claudeAnswer}")); }\n`)
  // run from the repository, so that rust-toolchain.toml picks the compiler
  execFileSync('rustc', [source, '-o', fakeClaude], { cwd: import.meta.dirname, stdio: 'inherit' })
}
