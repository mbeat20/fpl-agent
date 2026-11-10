import type { AttentionBoard, AttentionRow, ConsensusMovement } from '../types'
import { movementLabel, signed } from '../types'

/**
 * Issue 28: which players trusted creators are talking about, and what moved.
 *
 * Ordered by movement, never by volume. "Salah, 12 creators" is true every
 * week and says nothing; "1 → 7 in four days" is the story.
 *
 * This is deliberately NOT a shortlist to buy from. Ticket 19 scoped the
 * bandwagon meter to the user's own instinct-players precisely so it could not
 * be used to go shopping for bandwagons; inverting that scoping means carrying
 * the framing here instead — `preferences.md` treats heavy coverage as a risk,
 * not an endorsement. So there is no ranking of quality and no "top picks".
 *
 * GROUPED BY MOVEMENT, and that is the fix this panel needed most.
 *
 * `row.summary` comes from `consensus.py`, whose docstring is explicit that it
 * is written for the reasoning layer to read: ticket 18 found that a number
 * the model has to interpret gets interpreted inconsistently while a phrase it
 * can quote does not. So every surging row carries the same closing sentence,
 * by design, because the model needs it on every row it sees.
 *
 * On screen that produced eight consecutive rows ending "...a consensus this
 * fast-moving is a reason to check your own reasoning, not confirmation of
 * it." — which is the most important sentence on the panel, repeated until it
 * became wallpaper. Trimming it in the backend would fix the screen by
 * degrading what the model reads.
 *
 * So the grouping does it presentationally: rows are bucketed by
 * `row.movement`, the shared caveat is stated once as the group's heading, and
 * each row keeps only its own numbers. Nothing is edited out of a creator's
 * data and the model's input is untouched.
 */

// Ordered by how much a reader should care, not alphabetically. A surge is the
// thing worth pausing on; an absence of coverage is the footnote.
const GROUPS: { movement: ConsensusMovement; caveat: string }[] = [
  {
    movement: 'surging',
    caveat:
      'Up sharply inside a week. A consensus moving this fast is a reason to check your own reasoning, not confirmation of it.',
  },
  {
    movement: 'cooling',
    caveat:
      'Positive coverage has fallen. Something changed the picture — worth finding out what before acting on the older takes.',
  },
  {
    movement: 'new_coverage',
    caveat:
      'From no coverage at all last gameweek. Nobody changed their mind; they have just entered the conversation, so this is new attention rather than a measurable swing.',
  },
  {
    movement: 'settled',
    caveat:
      'Held steadily across gameweeks rather than a recent swing. Where coverage is heavy and settled, not owning them is the riskier position.',
  },
  {
    movement: 'dropped_off',
    caveat:
      'Talked about last gameweek and not this one. The older takes are still on record but nobody is restating them.',
  },
  { movement: 'steady', caveat: 'No meaningful movement either way.' },
  {
    movement: 'no_coverage',
    caveat:
      'No trusted creator has taken a position either gameweek. That is an absence of coverage, not agreement.',
  },
]

export default function AttentionPanel({ board }: { board: AttentionBoard }) {
  if (board.rows.length === 0) return null

  const grouped = GROUPS.map((group) => ({
    ...group,
    rows: board.rows.filter((row) => row.movement === group.movement),
  })).filter((group) => group.rows.length > 0)

  return (
    <div className="panel">
      <div className="panel-head">
        <div>
          <div className="panel-kicker">What creators are talking about</div>
          <div className="pick-name">
            {board.planning_gameweek !== null ? `GW${board.planning_gameweek}` : 'This week'}
          </div>
        </div>
        <span className="attention-slice">
          {board.returned} of {board.total_players}
        </span>
      </div>

      <div className="panel-divider" />

      {grouped.map((group) => {
        const { label, tone } = movementLabel(group.movement)
        return (
          <section key={group.movement} className="attention-group">
            <div className="attention-group-head">
              <span className={`consensus-tag consensus-tag-${tone}`}>{label}</span>
              <span className="attention-group-count">
                {group.rows.length} {group.rows.length === 1 ? 'player' : 'players'}
              </span>
            </div>
            <p className="attention-group-caveat">{group.caveat}</p>
            <div className="attention-list">
              {group.rows.map((row) => (
                <AttentionRowView key={row.player_name} row={row} tone={tone} />
              ))}
            </div>
          </section>
        )
      })}

      {/* Set only when gameweek attribution fell back to a proxy or to the
          known-unreliable stored tag — the counts mean something weaker then. */}
      {board.note && <div className="consensus-caveat">{board.note}</div>}

      <div className="consensus-footnote">
        Counted per creator, not per post, and ordered by what moved rather than by what is
        loudest. A high count is as often a warning as an endorsement — the call stays yours.
      </div>
    </div>
  )
}

function AttentionRowView({ row, tone }: { row: AttentionRow; tone: string }) {
  return (
    <div className="attention-row">
      <span className="consensus-player">{row.player_name}</span>

      <span className="consensus-numbers">
        <span className="consensus-now">
          {row.current.creators_positive}
          <span className="consensus-of"> / {row.current.creators_covering}</span>
        </span>
        <span className="consensus-when">positive</span>
        <span className="consensus-arrow">←</span>
        <span className="consensus-then">{row.previous.creators_positive}</span>
        {row.change.positive !== 0 && (
          <span className={`consensus-delta consensus-delta-${tone}`}>
            {signed(row.change.positive)}
          </span>
        )}
      </span>

      {row.creators_last_48h > 0 && (
        <span className="attention-fresh" title={`${row.creator_names_last_48h.join(', ')}`}>
          {row.creators_last_48h} in 48h
        </span>
      )}

      {/* The model-facing sentence is kept as the row's title rather than
          rendered: it is the authoritative wording and worth being able to
          read, but on screen it is what the group heading already said. */}
      <span className="attention-why" title={row.summary} aria-label={row.summary}>
        ⓘ
      </span>
    </div>
  )
}
