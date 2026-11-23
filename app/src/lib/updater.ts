import { invoke } from '@tauri-apps/api/core'
import { relaunch } from '@tauri-apps/plugin-process'
import { check, type Update } from '@tauri-apps/plugin-updater'

export interface AvailableUpdate {
  version: string
  notes: string
  handle: Update
}

export type DownloadProgress = { downloaded: number; total: number | null }

/** Asks the update endpoint for a newer version; `null` when the app is current. Rejects on network errors. */
export async function findUpdate(): Promise<AvailableUpdate | null> {
  const update = await check()
  if (!update) return null
  return { version: update.version, notes: update.body ?? '', handle: update }
}

/** Backs up the database, downloads and installs the update (signature is verified by the plugin), then restarts. */
export async function installUpdate(
  update: AvailableUpdate,
  onProgress: (progress: DownloadProgress) => void,
): Promise<void> {
  await invoke('backup_before_update')
  let downloaded = 0
  let total: number | null = null
  await update.handle.downloadAndInstall((event) => {
    if (event.event === 'Started') total = event.data.contentLength ?? null
    if (event.event === 'Progress') downloaded += event.data.chunkLength
    onProgress({ downloaded, total })
  })
  await relaunch()
}
