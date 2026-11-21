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
  options: ReadonlyArray<{ value: T; label: string }>
  onChange: (value: T) => void
}

export function Segmented<T extends string>({ value, options, onChange }: SegmentedProps<T>) {
  return (
    <div role="radiogroup" className="inline-flex rounded-md border border-line-strong bg-sunken p-0.5">
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          role="radio"
          aria-checked={option.value === value}
          onClick={() => onChange(option.value)}
          className={`h-8 rounded px-4 text-sm outline-none focus-visible:ring-2 focus-visible:ring-accent ${
            option.value === value ? 'bg-accent text-accent-ink' : 'text-ink hover:bg-surface'
          }`}
        >
          {option.label}
        </button>
      ))}
    </div>
  )
}
