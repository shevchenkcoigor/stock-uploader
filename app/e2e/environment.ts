// Everything the tested app may write lives under a new temporary HOME, so a local run touches neither the
// developer's settings nor their database. The launcher creates it and the workers inherit the path. Nothing
// here removes files: the folder stays in the system temp directory, which the OS cleans.
import { mkdirSync, mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { encode } from 'jpeg-js'

export const home = (process.env.STOCK_E2E_HOME ??= mkdtempSync(join(tmpdir(), 'stock-e2e-')))
export const pictures = join(home, 'pictures')
export const database = join(home, 'db', 'state.db')
export const pictureNames = ['first.jpg', 'second.jpg']

const dataDir = process.platform === 'darwin' ? join(home, 'Library', 'Application Support') : join(home, '.local', 'share')
export const settingsFile = join(dataDir, 'com.stock-uploader.desktop', 'settings.json')

export const appEnv = {
  HOME: home,
  XDG_DATA_HOME: join(home, '.local', 'share'),
  XDG_CONFIG_HOME: join(home, '.config'),
  XDG_CACHE_HOME: join(home, '.cache'),
  STOCK_STATE_DB: database,
  // a path that does not exist: no scenario may reach the real `claude`
  STOCK_CLAUDE_PATH: join(home, 'no-claude'),
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
