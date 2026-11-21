import { listen } from '@tauri-apps/api/event'
import { ask } from '@tauri-apps/plugin-dialog'
import { useCallback, useEffect, useState } from 'react'
import { captionApi, type ModelChoice, type ScanProgress } from '../../lib/api'
import { captionSummary } from '../../lib/describe'
import { DATA_CHANGED_EVENT } from '../../lib/events'

const PROGRESS_EVENT = 'caption-progress'
const PERCENT = 100

interface Options {
  /** Show a result or an error under the table. */
  say: (text: string, error?: boolean) => void
}

/** Captioning through Claude: the model picker, a run with progress, cancel. */
export function useCaption({ say }: Options) {
  const [models, setModels] = useState<ModelChoice[]>([])
  const [model, setModel] = useState('')
  const [running, setRunning] = useState(false)
  const [progress, setProgress] = useState<ScanProgress | null>(null)

  useEffect(() => {
    void captionApi
      .models()
      .then((list) => {
        setModels(list.models)
        setModel(list.selected)
      })
      .catch((reason) => say(String(reason), true))
    const unlisten = listen<ScanProgress>(PROGRESS_EVENT, (event) => setProgress(event.payload))
    return () => void unlisten.then((stop) => stop())
  }, [say])

  /** `names` null: every new file. `describedCount` files among them already have text that is replaced. */
  const start = useCallback(
    async (names: string[] | null, redo: boolean, describedCount: number) => {
      if (running || !model) return
      if (describedCount > 0) {
        const agreed = await ask(
          `У файлов с описанием: ${describedCount}. Подписать их заново и заменить заголовок, ключевые слова и категорию?`,
          { title: 'Подписать заново?', kind: 'warning' },
        )
        if (!agreed) return
      }
      setRunning(true)
      setProgress(null)
      say('Подписываю…')
      try {
        const report = await captionApi.start(names, redo, model)
        const problems = report.errors.length > 0 ? ` · ошибок: ${report.errors.length}. ${report.errors[0]}` : ''
        say(captionSummary(report) + problems, report.errors.length > 0)
      } catch (reason) {
        say(`ОШИБКА: ${reason}`, true)
      } finally {
        setRunning(false)
        setProgress(null)
        window.dispatchEvent(new Event(DATA_CHANGED_EVENT))
      }
    },
    [running, model, say],
  )

  const cancel = () => void captionApi.cancel()
  const percent = progress && progress.total > 0 ? Math.floor((progress.done * PERCENT) / progress.total) : null

  return { models, model, setModel, running, progress, percent, start, cancel }
}
