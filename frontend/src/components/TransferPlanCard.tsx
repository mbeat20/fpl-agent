import type { TransferPlan } from '../types'
import { decisionLabel } from '../types'
import ConfidenceSignal from './ConfidenceSignal'
import EvidenceLine from './EvidenceLine'

export default function TransferPlanCard({ plan }: { plan: TransferPlan }) {
  const steps = [...plan.steps].sort((a, b) => a.gameweek - b.gameweek)

  return (
    <div className="panel">
      <div className="panel-head">
        <div>
          <div className="panel-kicker">Long-Term Plan · {plan.horizon}</div>
        </div>
        <ConfidenceSignal confidence={plan.confidence} />
      </div>

      <div className="plan-steps">
        {steps.map((step, i) => (
          <div className="plan-step" key={i}>
            <div className="plan-step-head">
              <span className="plan-gw">GW{step.gameweek}</span>
              {/* Same contract as RecommendationCard: a "roll" week names no
                  player, so the decision stands in for the pick. */}
              <span className="plan-step-pick">
                {step.pick ? step.pick.player_name : decisionLabel(step.decision)}
              </span>
            </div>

            {step.evidence.map((item, j) => (
              <EvidenceLine item={item} key={j} />
            ))}
            {step.risks.map((item, j) => (
              <div className="risk-line" key={j}>
                {item}
              </div>
            ))}
          </div>
        ))}
      </div>
    </div>
  )
}
