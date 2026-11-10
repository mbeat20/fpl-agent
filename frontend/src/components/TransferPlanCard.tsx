import type { TransferPlan } from '../types'
import ConfidenceSignal from './ConfidenceSignal'

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
              <span className="plan-step-pick">{step.pick.player_name}</span>
            </div>

            {step.evidence.map((item, j) => (
              <div className="evidence-line" key={j}>
                {item}
              </div>
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
