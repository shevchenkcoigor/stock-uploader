import { useState } from 'react'
import { Sidebar } from './components/Sidebar'
import { StatusLine } from './components/StatusLine'
import { PAGES } from './pages/pages'

export default function App() {
  const [currentId, setCurrentId] = useState(PAGES[0].id)
  const Current = (PAGES.find((page) => page.id === currentId) ?? PAGES[0]).component
  return (
    <div className="flex h-full flex-col">
      <div className="flex min-h-0 flex-1">
        <Sidebar pages={PAGES} currentId={currentId} onSelect={setCurrentId} />
        <main className="min-w-0 flex-1 bg-window">
          <Current />
        </main>
      </div>
      <StatusLine />
    </div>
  )
}
