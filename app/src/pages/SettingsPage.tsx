import { open } from '@tauri-apps/plugin-dialog'
import { CheckButton } from '../components/CheckResult'
import { Button, Field, NumberInput, Section, Segmented, TextInput } from '../components/form'
import { settingsApi, type ThemeMode } from '../lib/api'
import { useSettings, type SaveState } from '../lib/useSettings'

const MAX_BATCH_SIZE = 20_000
const MAX_CAPTION_WORKERS = 8
const MAX_IMAGES_PER_CALL = 20
const MAX_PORT = 65_535

const THEMES: ReadonlyArray<{ value: ThemeMode; label: string }> = [
  { value: 'system', label: 'Как в системе' },
  { value: 'light', label: 'Светлая' },
  { value: 'dark', label: 'Тёмная' },
]

const STATE_TEXT: Record<SaveState, string> = {
  idle: '',
  saving: 'Сохраняю…',
  saved: 'Сохранено',
  error: '',
}

function PathField({
  value,
  placeholder,
  onChange,
  directory,
}: {
  value: string
  placeholder: string
  onChange: (value: string) => void
  directory: boolean
}) {
  const browse = async () => {
    const chosen = await open({ directory, multiple: false, defaultPath: value || placeholder })
    if (typeof chosen === 'string') onChange(chosen)
  }
  return (
    <div className="flex gap-2">
      <TextInput value={value} placeholder={placeholder} onChange={onChange} mono />
      <Button onClick={() => void browse()}>Выбрать…</Button>
    </div>
  )
}

export function SettingsPage() {
  const model = useSettings()
  const { draft, view, change } = model

  if (!draft || !view) {
    return <p className="p-6 text-sm text-ink-muted">{model.error ?? 'Загрузка…'}</p>
  }

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto flex max-w-3xl flex-col gap-4 p-6">
        <header className="flex items-baseline justify-between">
          <h1 className="text-xl font-semibold">Настройки</h1>
          <span aria-live="polite" className={`text-sm ${model.state === 'error' ? 'text-danger' : 'text-ink-muted'}`}>
            {model.state === 'error' ? model.error : STATE_TEXT[model.state]}
          </span>
        </header>

        <Section
          title="Adobe Stock SFTP"
          hint="Пароль хранится в системной связке ключей (macOS Keychain), остальное в файле настроек."
        >
          <div className="grid grid-cols-[1fr_120px] gap-4">
            <Field label="Хост">
              <TextInput value={draft.sftpHost} onChange={(sftpHost) => change({ sftpHost })} />
            </Field>
            <Field label="Порт">
              <NumberInput value={draft.sftpPort} min={1} max={MAX_PORT} onChange={(sftpPort) => change({ sftpPort })} />
            </Field>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <Field label="Логин">
              <TextInput value={draft.sftpUser} onChange={(sftpUser) => change({ sftpUser })} />
            </Field>
            <Field label="Пароль">
              <TextInput
                type="password"
                value={model.password}
                onChange={model.setPassword}
                placeholder="оставьте пустым, чтобы не менять сохранённый"
              />
            </Field>
          </div>
          <CheckButton label="Проверить SFTP" beforeRun={model.flush} run={settingsApi.checkSftp} />
        </Section>

        <Section title="Описание через Claude" hint="Заголовки и ключевые слова генерирует локальный Claude Code.">
          <Field label="Путь к claude" hint="Пусто — искать автоматически.">
            <TextInput value={draft.claudePath} onChange={(claudePath) => change({ claudePath })} mono />
          </Field>
          <div className="grid grid-cols-3 gap-4">
            <Field label="Файлов за один «Caption»">
              <NumberInput
                value={draft.captionLimit}
                min={1}
                max={MAX_BATCH_SIZE}
                onChange={(captionLimit) => change({ captionLimit })}
              />
            </Field>
            <Field label="Параллельных вызовов">
              <NumberInput
                value={draft.captionWorkers}
                min={1}
                max={MAX_CAPTION_WORKERS}
                onChange={(captionWorkers) => change({ captionWorkers })}
              />
            </Field>
            <Field label="Картинок в вызове">
              <NumberInput
                value={draft.captionPerCall}
                min={1}
                max={MAX_IMAGES_PER_CALL}
                onChange={(captionPerCall) => change({ captionPerCall })}
              />
            </Field>
          </div>
          <CheckButton label="Проверить Claude" beforeRun={model.flush} run={settingsApi.checkClaude} />
        </Section>

        <Section title="Батчи и заголовки">
          <div className="grid grid-cols-2 gap-4">
            <Field label="Файлов в батче">
              <NumberInput
                value={draft.batchSize}
                min={1}
                max={MAX_BATCH_SIZE}
                onChange={(batchSize) => change({ batchSize })}
              />
            </Field>
            <Field label="Метка в конце заголовка" hint="Пусто — не добавлять.">
              <TextInput value={draft.aiTitleLabel} onChange={(aiTitleLabel) => change({ aiTitleLabel })} />
            </Field>
          </div>
        </Section>

        <Section title="Файлы и папки" hint="Пусто — место по умолчанию.">
          <Field label="Файл базы (state.db)">
            <PathField
              value={draft.dbPath}
              placeholder={view.defaultDbPath}
              onChange={(dbPath) => change({ dbPath })}
              directory={false}
            />
          </Field>
        </Section>

        <Section title="Внешний вид">
          <Segmented value={draft.theme} options={THEMES} onChange={(theme) => change({ theme })} />
        </Section>
      </div>
    </div>
  )
}
