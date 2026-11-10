import type { DeadlineChecklist, Suggestion } from '../types'
import { isTransferPlan } from '../types'
import { useEndpoint } from '../useEndpoint'
import DeadlinePanel from '../components/DeadlinePanel'
import PriceRiskPanel from '../components/PriceRiskPanel'
import RecommendationCard from '../components/RecommendationCard'
import TransferPlanCard from '../components/TransferPlanCard'

/**
 * The Friday page: how long have I got, is anything about my team broken, and
 * what does the coach think.
 *
 * The important change from the old single dashboard is that each panel loads
 * on its own. The eight panels used to share one `Promise.all` behind one
 * render gate that included `/team/suggestion` -- the only read on the page
 * that can trigger a Claude call -- so the countdown, which is a database read
 * and a subtraction, waited on it. Measured at around twenty seconds when the
 * cached suggestion's gameweek had rolled over, and the screen said "Loading
 * your squad" for all of it while the deadline it exists to show ticked down.
 *
 * Order on the page is by urgency, not by cost: the clock and the checklist
 * first because they have a deadline attached, the coach's read second because
 * it is advice you can take your time over, price movement last because it is
 * context rather than a decision.
 */
export default function DeadlinePage() {
  const checklist = useEndpoint<DeadlineChecklist>('/api/team/checklist')
  const suggestion = useEndpoint<Suggestion>('/api/team/suggestion')

  return (
    <div className="dashboard">
      {checklist.data && <DeadlinePanel checklist={checklist.data} />}
      {checklist.loading && <PanelSkeleton label="Reading your squad…" tall />}
      {checklist.failed && (
        <PanelNote>
          The deadline checklist couldn&rsquo;t be loaded, so the countdown and the squad
          checks are missing rather than clear.
        </PanelNote>
      )}

      {/* The billed panel, and the slow one. It gets a skeleton with an
          explanation rather than a bare spinner, because the honest wait is
          long enough that silence reads as a hang. */}
      {suggestion.loading && (
        <PanelSkeleton label="The coach is thinking — this one is a reasoning call, so it can take a minute." />
      )}
      {suggestion.failed && (
        <PanelNote>Couldn&rsquo;t reach the coach for this gameweek&rsquo;s read.</PanelNote>
      )}
      {!suggestion.loading && !suggestion.failed && suggestion.data === null && (
        <PanelNote>
          No read for this gameweek yet — refresh your FPL data and it will generate on the
          next visit.
        </PanelNote>
      )}
      {suggestion.data !== null &&
        (isTransferPlan(suggestion.data) ? (
          <TransferPlanCard plan={suggestion.data} />
        ) : (
          <RecommendationCard recommendation={suggestion.data} />
        ))}

      {checklist.data && <PriceRiskPanel price={checklist.data.price_risk} />}
    </div>
  )
}

function PanelSkeleton({ label, tall = false }: { label: string; tall?: boolean }) {
  return (
    <div className={`panel panel-skeleton ${tall ? 'panel-skeleton-tall' : ''}`}>
      <div className="skeleton-label">{label}</div>
    </div>
  )
}

function PanelNote({ children }: { children: React.ReactNode }) {
  return <div className="panel panel-note">{children}</div>
}
