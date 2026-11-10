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
        <span className="squad-club">{player.club}</span>
      </div>
      <div className="squad-row-meta">
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

      <div className="panel-divider" />

      <div className="squad-list">
        {team.players.map((player) => (
          <SquadRow key={player.id} player={player} />
        ))}
      </div>
    </div>
  )
}
