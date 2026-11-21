import type { PageDef } from '../pages/pages'

interface Props {
  pages: PageDef[]
  currentId: string
  onSelect: (id: string) => void
}

function NavButton({ page, active, onSelect }: { page: PageDef; active: boolean; onSelect: (id: string) => void }) {
  return (
    <button
      type="button"
      onClick={() => onSelect(page.id)}
      className={`h-[30px] w-full px-4 text-left text-sm outline-none focus-visible:ring-2 focus-visible:ring-accent ${
        active ? 'bg-accent-soft font-medium text-ink' : 'text-ink hover:bg-sunken'
      }`}
    >
      {page.title}
    </button>
  )
}

export function Sidebar({ pages, currentId, onSelect }: Props) {
  const top = pages.filter((page) => !page.bottom)
  const bottom = pages.filter((page) => page.bottom)
  return (
    <nav className="flex w-[180px] shrink-0 flex-col justify-between border-r border-line bg-window py-3">
      <div>
        {top.map((page) => (
          <NavButton key={page.id} page={page} active={page.id === currentId} onSelect={onSelect} />
        ))}
      </div>
      <div>
        {bottom.map((page) => (
          <NavButton key={page.id} page={page} active={page.id === currentId} onSelect={onSelect} />
        ))}
      </div>
    </nav>
  )
}
