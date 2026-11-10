import type { Player, Team } from '../types'

const STATUS_LABEL: Record<string, string> = {
  a: 'Available',
  d: 'Doubtful',
  i: 'Injured',
  s: 'Suspended',
  u: 'Unavailable',
}

function statusClass(status: string | null): string {
  if (status === 'd') return 'status-warn'
  if (status === 'i' || status === 's' || status === 'u') return 'status-danger'
  return 'status-ok'
}

function SquadRow({ player }: { player: Player }) {
  return (
    <div className="squad-row">
      <div className="squad-row-main">
        <span className="squad-pos">{player.position}</span>
        <span className="squad-name">{player.name}</span>
        {/* Issue 20: the armband. Shown here so the checklist's captain line
            can be checked against the squad rather than taken on trust. */}
        {player.is_captain && <span className="squad-armband">C</span>}
        {player.is_vice_captain && <span className="squad-armband squad-armband-vice">V</span>}
        <span className="squad-club">{player.club}</span>
      </div>
      <div className="squad-row-meta">
        {/* Issue 20: a fit player who is not being picked. `status` cannot
            express this, which is why it went unseen until it showed up as
            bad form weeks later. */}
        {player.rotation_risk && <span className="status-pill status-warn">Rotation</span>}
        <span className="squad-price">£{player.price.toFixed(1)}</span>
        {player.status && player.status !== 'a' && (
          <span className={`status-pill ${statusClass(player.status)}`}>
            {STATUS_LABEL[player.status] ?? player.status}
          </span>
        )}
      </div>
    </div>
  )
}

export default function SquadList({ team }: { team: Team }) {
  // Issue 20: squad_position 1-11 is the XI, 12-15 the bench in the order it
  // would come on. Null for a row seeded before ingestion ever ran -- those
  // fall through to a single undifferentiated list rather than being
  // arbitrarily assigned to one side of a split that does not exist yet.
  const ranked = team.players.filter((p) => p.squad_position !== null)
  const split = ranked.length > 0
  const xi = ranked.filter((p) => (p.squad_position as number) <= 11)
  const bench = ranked.filter((p) => (p.squad_position as number) > 11)

  return (
    <div className="panel">
      <div className="panel-head">
        <div>
          <div className="panel-kicker">Squad</div>
          <div className="pick-name">{team.name}</div>
        </div>
        {team.current_gameweek !== null && (
          <span className="plan-gw">GW{team.current_gameweek}</span>
        )}
      </div>

      {/* The caveat that makes the XI below honest: FPL only publishes picks
          once a deadline has locked, so whenever squad_gameweek trails the
          gameweek being planned, this is last week's team. Saying "GW3" over
          a GW2 lineup would be the panel's most confident lie. */}
      {split && team.squad_gameweek !== null && team.squad_gameweek !== team.current_gameweek && (
        <div className="squad-asof">
          Lineup as of GW{team.squad_gameweek} — FPL only publishes picks after a deadline
          locks, so this is not yet a confirmed team for GW{team.current_gameweek}.
        </div>
      )}

      <div className="panel-divider" />

      {split ? (
        <>
          <div className="squad-list">
            {xi.map((player) => (
              <SquadRow key={player.id} player={player} />
            ))}
          </div>
          {bench.length > 0 && (
            <>
              <div className="squad-split">Bench, in the order they come on</div>
              <div className="squad-list squad-list-bench">
                {bench.map((player) => (
                  <SquadRow key={player.id} player={player} />
                ))}
              </div>
            </>
          )}
        </>
      ) : (
        <div className="squad-list">
          {team.players.map((player) => (
            <SquadRow key={player.id} player={player} />
          ))}
        </div>
      )}
    </div>
  )
}
