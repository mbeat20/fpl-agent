import type { CreatorConsensus, Instincts, Team } from '../types'
import { useEndpoint } from '../useEndpoint'
import ConsensusPanel from '../components/ConsensusPanel'
import InstinctPanel from '../components/InstinctPanel'
import SquadList from '../components/SquadList'

/**
 * The squad, and the user's own thinking about it.
 *
 * Instincts and consensus sit together here on purpose. Ticket 13 logs the
 * early-week note ("thinking Rogers -> Enzo"); ticket 19 attaches what
 * happened to creator opinion on those same players since. Together they
 * answer one question -- did my thinking evolve, or did I get herded -- and on
 * the old single dashboard they were four panels apart, which made the pairing
 * invisible. `/team/consensus` is scoped to instinct-players precisely so it
 * cannot become a way to go shopping for bandwagons, and that scoping only
 * makes sense next to the instincts themselves.
 *
 * A straight port otherwise: this page reuses the existing panels unchanged
 * and is the next one due a proper design pass.
 */
export default function SquadPage() {
  const team = useEndpoint<Team>('/api/team')
  const instincts = useEndpoint<Instincts>('/api/team/instincts')
  const consensus = useEndpoint<CreatorConsensus[]>('/api/team/consensus')

  return (
    <div className="dashboard">
      {team.data && <SquadList team={team.data} />}
      {team.loading && <div className="loading-note">Loading your squad…</div>}
      {team.failed && (
        <div className="panel panel-note">
          Couldn&rsquo;t load your squad. If you have never run an ingestion, there is nothing
          to show yet.
        </div>
      )}

      {instincts.data && <InstinctPanel notes={instincts.data.notes} />}
      {consensus.data && <ConsensusPanel items={consensus.data} />}
    </div>
  )
}
