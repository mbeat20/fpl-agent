import type { RoadmapCheckIn } from '../types'
import { useEndpoint } from '../useEndpoint'
import RoadmapPanel from '../components/RoadmapPanel'

/**
 * The multi-week plan the user is following.
 *
 * A page rather than a panel because the check-in is only half of ticket 25.
 * The other half is the VERSION HISTORY -- `/team/roadmap/versions` is built,
 * tested and, until this page existed, fetched by nothing at all. Roadmaps are
 * versioned rather than overwritten specifically so "last week's plan said
 * something different" is answerable, and that question has had no surface to
 * be asked on.
 *
 * The check-in is ported unchanged here; the version timeline is the next
 * thing due on this page.
 */
export default function PlanPage() {
  const checkIn = useEndpoint<RoadmapCheckIn>('/api/team/roadmap/check-in')

  return (
    <div className="dashboard">
      {checkIn.loading && <div className="loading-note">Loading your plan…</div>}
      {checkIn.failed && (
        <div className="panel panel-note">Couldn&rsquo;t load the roadmap check-in.</div>
      )}

      {/* RoadmapPanel renders nothing when no roadmap is stored, so the empty
          case is stated here instead -- on a dedicated page a blank screen is
          indistinguishable from a broken one. */}
      {checkIn.data && checkIn.data.roadmap === null ? (
        <div className="panel panel-note">
          No plan stored yet. Ask the coach a multi-week question — &ldquo;plan my next four
          gameweeks&rdquo; — and the plan it gives you is kept here, versioned, so it can be
          checked against what actually happens.
        </div>
      ) : (
        checkIn.data && <RoadmapPanel checkIn={checkIn.data} />
      )}
    </div>
  )
}
