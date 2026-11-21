export function Placeholder({ title }: { title: string }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 text-ink-muted">
      <h1 className="text-xl font-semibold text-ink">{title}</h1>
      <p className="text-sm">Эта страница ещё не перенесена с PySide.</p>
    </div>
  )
}
