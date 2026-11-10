import type { CreatorConsensus } from '../types'
import { movementLabel, signed } from '../types'

/**
 * Issue 19: creator consensus movement for the players the user has a live
 * Instinct about.
 *
 * The panel is built around the *change*, not the level, because that is the
 * only thing that separates a template pick from a bandwagon. So the bar
 * shows both weeks side by side and the delta is stated in a signed number
 * rather than left to be worked out from two counts.
 *
 * It reports and stops. The ticket is explicit that the app's job here is to
 * make the influence visible, not to rule on it -- so there is no verdict, no
 * recommendation, and 'Bandwagon forming' is styled as a flag to look harder
 * rather than as a red light.
 */
export default function ConsensusPanel({ items }: { items: CreatorConsensus[] }) {
  // Nothing logged this gameweek, or nothing logged that names a player we
  // recognise: render nothing at all rather than an empty panel explaining
  // its own absence.
  if (items.length === 0) return null

  return (
    <div className="panel">
      <div className="panel-kicker">What changed since you logged that</div>

      <div className="panel-divider" />

      <div className="consensus-list">
        {items.map((item) => (
          <ConsensusRow key={item.player_name} item={item} />
        ))}
      </div>

      <div className="consensus-footnote">
        Counted per creator, not per post. A high count is as often a warning as an
        endorsement — the call stays yours.
      </div>
    </div>
  )
}

function ConsensusRow({ item }: { item: CreatorConsensus }) {
  const { label, tone } = movementLabel(item.movement)
  const current = item.current.creators_positive
  const previous = item.previous.creators_positive
  const gw = item.current.gameweek
  const prevGw = item.previous.gameweek

  return (
    <div className="consensus-row">
      <div className="consensus-head">
        <span className="consensus-player">{item.player_name}</span>
        <span className={`consensus-tag consensus-tag-${tone}`}>{label}</span>
      </div>

      <div className="consensus-numbers">
        <span className="consensus-now">
          {current}
          <span className="consensus-of"> / {item.roster_size}</span>
        </span>
        <span className="consensus-when">positive{gw !== null ? ` in GW${gw}` : ''}</span>
        <span className="consensus-arrow">←</span>
        <span className="consensus-then">
          {previous}
          {prevGw !== null ? ` in GW${prevGw}` : ' before'}
        </span>
        {item.change.positive !== 0 && (
          <span className={`consensus-delta consensus-delta-${tone}`}>
            {signed(item.change.positive)}
          </span>
        )}
      </div>

      <div className="consensus-summary">{item.summary}</div>

      {item.instincts.map((note, i) => (
        <div className="consensus-instinct" key={i}>
          <span className="consensus-instinct-kicker">You logged</span>
          {note.text}
        </div>
      ))}

      {/* The backend sets `note` only when gameweek attribution fell back to a
          proxy or to the known-unreliable stored tag. Surfacing it keeps a
          weaker number from being read as a firm one. */}
      {item.note && <div className="consensus-caveat">{item.note}</div>}
    </div>
  )
}
