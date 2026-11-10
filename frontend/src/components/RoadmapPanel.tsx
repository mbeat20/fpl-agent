import type { PlanStep, RoadmapChange, RoadmapCheckIn } from '../types'
import { decisionLabel } from '../types'

/**
 * Issue 25: the multi-week plan the user is following, and whether it still
 * holds.
 *
 * Ticket 12 shipped multi-week plans and they evaporated the moment the
 * response rendered. This is the same plan a week later — which is the only
 * point at which it becomes possible to say whether the user drifted from it,
 * or the world moved under it.
 *
 * Like ticket 23's checklist it reports and stops. It marks the steps that no
 * longer hold and says why; it does not write a replacement, because revising
 * a plan is a reasoning call the user chooses to make rather than something
 * the app does to their plan behind their back.
 */
export default function RoadmapPanel({ checkIn }: { checkIn: RoadmapCheckIn }) {
  const roadmap = checkIn.roadmap
  // No roadmap stored yet: render nothing rather than a panel explaining its
  // own absence, same as InstinctPanel and ConsensusPanel.
  if (roadmap === null) return null

  const byStep = new Map<number, RoadmapChange[]>()
  for (const change of checkIn.changes) {
    byStep.set(change.step_index, [...(byStep.get(change.step_index) ?? []), change])
  }

  const elapsed = new Set(checkIn.elapsed_steps)
  const hits = checkIn.revalidation?.total_hit_cost ?? 0

  return (
    <div className="panel">
      <div className="panel-head">
        <div>
          <div className="panel-kicker">
            Roadmap v{roadmap.version}
            {roadmap.status === 'completed' && ' · completed'}
          </div>
          <div className="pick-name">{roadmap.plan.horizon}</div>
        </div>
        <span className={`roadmap-state roadmap-state-${checkIn.still_valid ? 'ok' : 'warn'}`}>
          {checkIn.still_valid ? 'On track' : 'Drifted'}
        </span>
      </div>

      <div className="roadmap-summary">{checkIn.summary}</div>

      <div className="panel-divider" />

      <div className="roadmap-steps">
        {roadmap.plan.steps.map((step, index) => (
          <RoadmapStep
            key={index}
            step={step}
            index={index}
            elapsed={elapsed.has(index)}
            changes={byStep.get(index) ?? []}
          />
        ))}
      </div>

      {/* Stated once, at the bottom. A plan whose steps are individually
          reasonable can still cost 12 points that nobody added up. */}
      {hits > 0 && (
        <div className="roadmap-cost">
          This plan spends {hits} points on hits across{' '}
          {checkIn.revalidation?.transfers_planned} transfers.
        </div>
      )}

      {/* Fixture moves that do not land on a step are still worth knowing —
          a double appearing nearby is a reason to reopen the plan even when
          nothing in it broke. */}
      {checkIn.fixture_changes.length > 0 && (
        <div className="roadmap-fixtures">
          <div className="section-h">Fixtures that moved since this was made</div>
          {checkIn.fixture_changes.map((change, i) => (
            <div className="roadmap-fixture" key={i}>
              {change.message}
            </div>
          ))}
        </div>
      )}

      {checkIn.note && <div className="roadmap-note">{checkIn.note}</div>}
    </div>
  )
}

function RoadmapStep({
  step,
  index,
  elapsed,
  changes,
}: {
  step: PlanStep
  index: number
  elapsed: boolean
  changes: RoadmapChange[]
}) {
  // An elapsed step is not a broken one — the plan simply progressed — so it
  // is dimmed rather than flagged, and its 'elapsed' note is not repeated as
  // a problem.
  const problems = changes.filter((c) => c.kind !== 'elapsed')
  const tone = elapsed ? 'past' : problems.length > 0 ? 'broken' : 'ahead'

  return (
    <div className={`roadmap-step roadmap-step-${tone}`}>
      <div className="roadmap-step-head">
        <span className="plan-gw">GW{step.gameweek}</span>
        <span className="roadmap-step-decision">{decisionLabel(step.decision)}</span>
        {step.pick && <span className="roadmap-step-pick">{step.pick.player_name}</span>}
        {step.player_out && (
          <span className="roadmap-step-out">out: {step.player_out.player_name}</span>
        )}
        {elapsed && <span className="roadmap-step-tag">passed</span>}
      </div>

      {problems.map((change, i) => (
        <div className="roadmap-step-problem" key={i}>
          {change.message}
        </div>
      ))}

      {/* Step index is shown only when something is wrong with it, because
          that is the handle the user needs to say "redo step 3". */}
      {problems.length > 0 && <div className="roadmap-step-ref">Step {index + 1}</div>}
    </div>
  )
}
