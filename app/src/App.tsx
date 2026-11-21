import { useState } from 'react'
import { Sidebar } from './components/Sidebar'
import { StatusLine } from './components/StatusLine'
import { PAGES } from './pages/pages'

export default function App() {
  const [currentId, setCurrentId] = useState(PAGES[0].id)
  return (
    <div className="flex h-full flex-col">
      <div className="flex min-h-0 flex-1">
        <Sidebar pages={PAGES} currentId={currentId} onSelect={setCurrentId} />
        <main className="min-w-0 flex-1 bg-window">
          {PAGES.map(({ id, component: Page }) => (
            <div key={id} hidden={id !== currentId} className="h-full">
              <Page active={id === currentId} navigate={setCurrentId} />
            </div>
          ))}
        </main>
      </div>
      <StatusLine />
    </div>
  )
}
