import type { ConversationTurn } from '../types'
import RecommendationCard from './RecommendationCard'
import TransferPlanCard from './TransferPlanCard'

/**
 * Issue 27: the Ask tab as a conversation rather than a single answer.
 *
 * Before this, `App.tsx` held one `response` and replaced it on every submit.
 * So "why not Haaland?" was not a follow-up — it was a brand-new question
 * reaching a model with no idea what it had just said. `preferences.md` asks
 * that the final call be justifiable as the user's own reasoning, and you
 * cannot own a decision you were unable to question.
 *
 * A finalised turn renders as the full card, not as a paragraph. Ticket 17
 * went to some trouble to make evidence lines carry checkable, timestamped
 * citations, and flattening them into chat prose would throw exactly that
 * away — the transcript is where they matter most, because that is where the
 * user is arguing about whether the reasoning holds.
 */
export default function Transcript({ turns }: { turns: ConversationTurn[] }) {
  if (turns.length === 0) return null

  return (
    <div className="transcript">
      {turns.map((turn, i) => (
        <Turn key={i} turn={turn} />
      ))}
    </div>
  )
}

function Turn({ turn }: { turn: ConversationTurn }) {
  if (turn.role === 'user') {
    return (
      <div className="turn turn-user">
        <span className="turn-who">You</span>
        <div className="turn-said">{turn.text}</div>
      </div>
    )
  }

  return (
    <div className="turn turn-coach">
      <span className="turn-who">Coach</span>
      {turn.recommendation ? (
        <RecommendationCard recommendation={turn.recommendation} />
      ) : turn.plan ? (
        <>
          {/* Issue 27: a revised plan carries a note saying what changed
              about the one step the user questioned. It sits above the card
              because without it the user is handed a plan that differs from
              the one they were just looking at, with no statement of where
              or why. A plan that was not a revision has no note and renders
              exactly as before. */}
          {turn.text && <div className="turn-revision">{turn.text}</div>}
          <TransferPlanCard plan={turn.plan} />
        </>
      ) : (
        <div className="turn-said">{turn.text}</div>
      )}
    </div>
  )
}
