import type { Recommendation } from '../types'
import ConfidenceSignal from './ConfidenceSignal'

export default function RecommendationCard({ recommendation }: { recommendation: Recommendation }) {
  return (
    <div className="panel">
      <div className="panel-head">
        <div>
          <div className="panel-kicker">Recommendation</div>
          <div className="pick-name">{recommendation.pick.player_name}</div>
        </div>
        <ConfidenceSignal confidence={recommendation.confidence} />
      </div>

      <div className="panel-divider" />

      <div className="section-h">Evidence</div>
      {recommendation.evidence.map((item, i) => (
        <div className="evidence-line" key={i}>
          {item}
        </div>
      ))}

      {recommendation.risks.length > 0 && (
        <>
          <div className="section-h" style={{ marginTop: 14 }}>
            Risks
          </div>
          {recommendation.risks.map((item, i) => (
            <div className="risk-line" key={i}>
              {item}
            </div>
          ))}
        </>
      )}

      <div className="alt-row">
        ALT → <b>{recommendation.alternative.player_name}</b> — {recommendation.alternative.reason}
      </div>
    </div>
  )
}
