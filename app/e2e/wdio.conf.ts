import { mkdirSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { appEnv, writeFakeClaude, writePictures } from './environment.ts'

const binary = resolve(import.meta.dirname, '../src-tauri/target/debug/stock-uploader')
const artifacts = resolve(import.meta.dirname, 'artifacts')
// a constant of its own: WebdriverIO's capability type does not list `tauri:options`
const capability = { browserName: 'tauri', 'tauri:options': { application: binary } }

export const config: WebdriverIO.Config = {
  runner: 'local',
  specs: ['./specs/**/*.e2e.ts'],
  maxInstances: 1,
  // `debug` would print the whole environment of the app, including CI variables
  logLevel: 'info',
  outputDir: join(artifacts, 'logs'),
  services: [
    [
      '@wdio/tauri-service',
      {
        driverProvider: 'embedded',
        appBinaryPath: binary,
        env: appEnv,
        captureBackendLogs: true,
        logDir: join(artifacts, 'logs'),
      },
    ],
  ],
  capabilities: [capability],
  framework: 'mocha',
  mochaOpts: { timeout: 60_000 },
  reporters: ['spec'],
  waitforTimeout: 15_000,
  onPrepare() {
    mkdirSync(join(artifacts, 'screenshots'), { recursive: true })
    writePictures()
    writeFakeClaude()
  },
}
