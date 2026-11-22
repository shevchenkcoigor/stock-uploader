import type { ReactNode } from 'react'

const inputClass =
  'h-9 w-full rounded-md border border-line-strong bg-surface px-3 text-sm text-ink outline-none select-text placeholder:text-ink-muted focus-visible:ring-2 focus-visible:ring-accent'

export function Section({ title, hint, children }: { title: string; hint?: string; children: ReactNode }) {
  return (
    <section className="rounded-lg border border-line bg-surface p-5">
      <h2 className="text-base font-semibold">{title}</h2>
      {hint && <p className="mt-1 text-sm text-ink-muted">{hint}</p>}
      <div className="mt-4 flex flex-col gap-4">{children}</div>
    </section>
  )
}

export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="text-sm font-medium">{label}</span>
      {children}
      {hint && <span className="text-xs text-ink-muted">{hint}</span>}
    </label>
  )
}

interface TextProps {
  value: string
  onChange: (value: string) => void
  placeholder?: string
  type?: 'text' | 'password'
  mono?: boolean
}

export function TextInput({ value, onChange, placeholder, type = 'text', mono }: TextProps) {
  return (
    <input
      type={type}
      value={value}
      placeholder={placeholder}
      spellCheck={false}
      autoComplete="off"
      onChange={(event) => onChange(event.target.value)}
      className={`${inputClass} ${mono ? 'font-mono text-xs' : ''}`}
    />
  )
}

interface NumberProps {
  value: number
  min: number
  max: number
  onChange: (value: number) => void
}

export function NumberInput({ value, min, max, onChange }: NumberProps) {
  return (
    <input
      type="number"
      value={value}
      min={min}
      max={max}
      onChange={(event) => onChange(Number(event.target.value))}
      className={inputClass}
    />
  )
}

export function Button({
  children,
  onClick,
  disabled,
}: {
  children: ReactNode
  onClick: () => void
  disabled?: boolean
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="h-9 shrink-0 rounded-md border border-line-strong bg-surface px-3 text-sm outline-none hover:bg-sunken focus-visible:ring-2 focus-visible:ring-accent disabled:opacity-50"
    >
      {children}
    </button>
  )
}

interface SegmentedProps<T extends string> {
  value: T
  /** `label` may be an icon: then give `title`, it names the segment for the mouse and for screen readers. */
  options: ReadonlyArray<{ value: T; label: ReactNode; title?: string }>
  onChange: (value: T) => void
}

/** A row of mutually exclusive choices in one frame; the chosen one is tinted. */
export function Segmented<T extends string>({ value, options, onChange }: SegmentedProps<T>) {
  return (
    <div role="radiogroup" className="inline-flex overflow-hidden rounded-lg border border-line-strong bg-surface">
      {options.map((option, index) => (
        <button
          key={option.value}
          type="button"
          role="radio"
          aria-checked={option.value === value}
          aria-label={option.title}
          title={option.title}
          onClick={() => onChange(option.value)}
          className={`flex h-9 min-w-10 items-center justify-center px-3 text-sm text-ink outline-none focus-visible:z-10 focus-visible:ring-2 focus-visible:ring-accent ${
            index > 0 ? 'border-l border-line-strong' : ''
          } ${option.value === value ? 'bg-accent-soft' : 'hover:bg-sunken'}`}
        >
          {option.label}
        </button>
      ))}
    </div>
  )
}
