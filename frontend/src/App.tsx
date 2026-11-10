import { useState } from 'react'
import RefreshControl from './components/RefreshControl'
import TickerHeader from './components/TickerHeader'
import CoachPage from './pages/CoachPage'
import DeadlinePage from './pages/DeadlinePage'
import DiscoursePage from './pages/DiscoursePage'
import PlanPage from './pages/PlanPage'
import SquadPage from './pages/SquadPage'
import Link from './components/Link'
import { ROUTES, useRoute, type RoutePath } from './router'

/**
 * The shell: navigation, the data-freshness control, and whichever page the
 * URL names.
 *
 * This replaces a two-value `useState<View>`. The old Team tab was eight
 * panels of unrelated purpose in one 640px column -- deadline triage, squad
 * reference, plan tracking and creator discourse, all stacked, all loaded
 * together -- and two of those panels were explicitly not about the user's
 * team at all. Splitting them by the job they do is the point; the URL is what
 * makes each one reloadable and linkable, which matters most on the page whose
 * content is a countdown you refresh to watch.
 *
 * `dataVersion` keys the page. When an ingestion run finishes, the pages'
 * fetches are stale, and a finished refresh that leaves the screen showing
 * pre-refresh numbers is the one way the refresh control could be actively
 * misleading -- the user would watch the data update and then read the old
 * version of it.
 */

const PAGES: Record<RoutePath, { title: string; render: () => React.ReactNode }> = {
  '/': { title: 'Deadline', render: () => <DeadlinePage /> },
  '/squad': { title: 'My squad', render: () => <SquadPage /> },
  '/coach': { title: 'Ask the coach', render: () => <CoachPage /> },
  '/plan': { title: 'My plan', render: () => <PlanPage /> },
  '/discourse': { title: 'The discourse', render: () => <DiscoursePage /> },
}

export default function App() {
  const route = useRoute()
  const [dataVersion, setDataVersion] = useState(0)
  const page = PAGES[route]

  return (
    <>
      <TickerHeader />
      {/* Wide for the boards, narrow for the conversation. Set on the shell
          rather than inside each page so the header and the content share one
          measure and the nav does not shift when you change pages. */}
      <main className={`main ${route === '/coach' ? 'main-narrow' : 'main-wide'}`}>
        <header className="app-head">
          <div>
            <span className="eyebrow">FPL Coach</span>
            <h1>{page.title}</h1>
          </div>
          <RefreshControl onRefreshed={() => setDataVersion((v) => v + 1)} />
        </header>

        <nav className="nav" aria-label="Sections">
          {ROUTES.map((item) => (
            <Link
              key={item.path}
              to={item.path}
              className={`nav-item ${route === item.path ? 'active' : ''}`}
              aria-current={route === item.path ? 'page' : undefined}
            >
              {item.label}
            </Link>
          ))}
        </nav>

        <div key={dataVersion}>{page.render()}</div>
      </main>
    </>
  )
}
