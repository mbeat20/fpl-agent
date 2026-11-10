import { useState } from 'react'
import type { PriceChangePredictions, PricePrediction } from '../types'

/**
 * Price movement in the user's own squad.
 *
 * Split out of `DeadlinePanel` and given its own card, because it was the
 * third thing inside a card already doing two jobs — and because it needed a
 * change the checklist did not: it was listing all seventeen squad players,
 * every direction, with raw transfer counts. Seventeen rows of
 * "Net -421811 transfers this gameweek (20.3% owned)" is a data dump, and the
 * two or three rows that actually matter were indistinguishable from the
 * fourteen that did not.
 *
 * So the top few each way are shown and the rest collapse behind a
 * disclosure. Not dropped — a player you own moving at all is your team value
 * moving, and the full list is one click away.
 *
 * The framing is deliberately a RANKING, not a prediction. The score behind
 * the order is net transfers divided by ownership (predict_price_changes.py),
 * which approximates FPL's undocumented, ownership-scaled threshold — it does
 * not know it. "Most likely to move" is honest; "will fall tonight" would be
 * inventing a certainty nobody has, on the one screen where the user is about
 * to act on it.
 */

// Three each way. Enough that a genuine cluster of movement is visible,
// few enough that the card stays glanceable next to a countdown.
const PROMINENT = 3

export default function PriceRiskPanel({ price }: { price: PriceChangePredictions }) {
  const [showAll, setShowAll] = useState(false)

  if (price.rising.length === 0 && price.falling.length === 0) return null

  // Falling first: for an owner a fall is value leaking away and a rise is
  // value earned, so the two mean opposite things and the bad news leads.
  const leadFalling = price.falling.slice(0, PROMINENT)
  const leadRising = price.rising.slice(0, PROMINENT)
  const rest = [...price.falling.slice(PROMINENT), ...price.rising.slice(PROMINENT)]

  return (
    <div className="panel">
      <div className="panel-head">
        <div>
          <div className="panel-kicker">Price movement in your squad</div>
          <div className="panel-sub">
            Ranked by transfers relative to ownership. FPL does not publish the threshold, so
            this is an ordering, not a prediction that a price will change tonight.
          </div>
        </div>
      </div>

      <div className="panel-divider" />

      <div className="price-list">
        {leadFalling.map((p) => (
          <PriceRow key={p.player_id} prediction={p} />
        ))}
        {leadRising.map((p) => (
          <PriceRow key={p.player_id} prediction={p} />
        ))}
      </div>

      {rest.length > 0 && (
        <>
          {showAll && (
            <div className="price-list price-list-rest">
              {rest.map((p) => (
                <PriceRow key={p.player_id} prediction={p} />
              ))}
            </div>
          )}
          <button
            type="button"
            className="price-more"
            onClick={() => setShowAll((s) => !s)}
            aria-expanded={showAll}
          >
            {showAll
              ? 'Hide the rest'
              : `${rest.length} more moving, further from a threshold`}
          </button>
        </>
      )}
    </div>
  )
}

function PriceRow({ prediction }: { prediction: PricePrediction }) {
  const falling = prediction.direction === 'fall'
  // The tool appends this to the evidence string when the player has already
  // moved today, and it changes what the row means — a second move in the
  // same gameweek is far less likely — so it is pulled out as its own mark
  // rather than left to be truncated off the end of a long line, which is
  // what the single-line layout was doing to it.
  const alreadyMoved = prediction.evidence.includes('already moved')
  const net = prediction.net_transfers_event

  return (
    <div className="price-row">
      <span className={`price-arrow ${falling ? 'price-fall' : 'price-rise'}`}>
        {falling ? '▼' : '▲'}
      </span>
      <span className="price-name">{prediction.player_name}</span>
      <span className="price-figures">
        <span className={falling ? 'price-fall' : 'price-rise'}>
          {net > 0 ? '+' : ''}
          {/* en-GB rather than the browser locale: several locales group with
              a space, and "-421 811" in a monospace column reads as two
              numbers. FPL is a British game and the rest of the app is
              GBP-denominated, so the comma is both clearer and consistent. */}
          {net.toLocaleString('en-GB')}
        </span>
        <span className="price-owned">
          {prediction.ownership === null ? 'ownership unknown' : `${prediction.ownership}% owned`}
        </span>
      </span>
      {alreadyMoved && <span className="price-moved">moved today</span>}
    </div>
  )
}
