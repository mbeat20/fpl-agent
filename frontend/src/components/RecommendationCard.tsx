import type { Recommendation } from '../types'
import { decisionLabel } from '../types'
import ConfidenceSignal from './ConfidenceSignal'
import EvidenceLine from './EvidenceLine'

export default function RecommendationCard({ recommendation }: { recommendation: Recommendation }) {
  const { decision, pick, alternative } = recommendation

  return (
    <div className="panel">
      <div className="panel-head">
        <div>
          {/* A pick-less decision (a "roll") has no player to headline, so the
              decision itself becomes the headline rather than a player the
              recommendation never actually named. */}
          <div className="panel-kicker">{pick ? decisionLabel(decision) : 'Recommendation'}</div>
          <div className="pick-name">{pick ? pick.player_name : decisionLabel(decision)}</div>
        </div>
        <ConfidenceSignal confidence={recommendation.confidence} />
      </div>

      <div className="panel-divider" />

      <div className="section-h">Evidence</div>
      {recommendation.evidence.map((item, i) => (
        <EvidenceLine item={item} key={i} />
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

      {alternative && (
        <div className="alt-row">
          ALT → <b>{alternative.player_name}</b> — {alternative.reason}
        </div>
      )}
    </div>
  )
}
