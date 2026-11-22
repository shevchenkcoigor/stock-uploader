import { useState } from 'react'
import { Button } from './form'

type Outcome = { ok: boolean; text: string } | null

interface Props {
  label: string
  /** Runs the check; the settings are saved first, and an invalid form stops it. */
  run: () => Promise<string>
  beforeRun: () => Promise<boolean>
}

export function CheckButton({ label, run, beforeRun }: Props) {
  const [busy, setBusy] = useState(false)
  const [outcome, setOutcome] = useState<Outcome>(null)

  const start = async () => {
    setBusy(true)
    setOutcome(null)
    try {
      if (!(await beforeRun())) return
      setOutcome({ ok: true, text: await run() })
    } catch (reason) {
      setOutcome({ ok: false, text: String(reason) })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex flex-col gap-2">
      <div>
        <Button onClick={() => void start()} disabled={busy}>
          {busy ? 'Проверяю…' : label}
        </Button>
      </div>
      {outcome && (
        <p
          role="status"
          className={`whitespace-pre-line select-text rounded-md px-3 py-2 text-sm ${
            outcome.ok ? 'bg-sunken text-success' : 'bg-sunken text-danger'
          }`}
        >
          {outcome.text}
        </p>
      )}
    </div>
  )
}
