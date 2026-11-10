import { useEffect, useState } from 'react'
import type {
  AttentionBoard,
  CreatorConsensus,
  DeadlineChecklist,
  Instincts,
  RoadmapCheckIn,
  ThemeBoard,
  Suggestion,
  Team,
} from '../types'
import { isTransferPlan } from '../types'
import AttentionPanel from './AttentionPanel'
import ConsensusPanel from './ConsensusPanel'
import DeadlinePanel from './DeadlinePanel'
import InstinctPanel from './InstinctPanel'
import RecommendationCard from './RecommendationCard'
import RoadmapPanel from './RoadmapPanel'
import SquadList from './SquadList'
import ThemesPanel from './ThemesPanel'
import TransferPlanCard from './TransferPlanCard'

export default function TeamDashboard() {
  const [team, setTeam] = useState<Team | null>(null)
  const [instincts, setInstincts] = useState<Instincts | null>(null)
  // Issue 23: the deadline countdown and checklist. Like consensus, this is
  // allowed to fail without taking the dashboard with it -- but unlike
  // consensus it is the first thing on the screen, so its absence is
  // conspicuous rather than silent.
  const [checklist, setChecklist] = useState<DeadlineChecklist | null>(null)
  // Issue 25: the stored multi-week plan and whether it still holds. Null
  // until one has been created by asking a long-horizon question.
  const [checkIn, setCheckIn] = useState<RoadmapCheckIn | null>(null)
  // Issue 19: consensus movement for players the user has an Instinct on.
  // Defaults to [] rather than null so a failure to load it can never block
  // the dashboard -- it is context on a decision, not the decision.
  const [consensus, setConsensus] = useState<CreatorConsensus[]>([])
  // Issue 28: the discourse board. Additive like consensus -- a failure
  // here leaves the panel absent rather than breaking the dashboard.
  const [attention, setAttention] = useState<AttentionBoard | null>(null)
  const [themes, setThemes] = useState<ThemeBoard | null>(null)
  // undefined = still loading, null = loaded but nothing generated yet.
  const [suggestion, setSuggestion] = useState<Suggestion | undefined>(undefined)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false

    async function load() {
      try {
        const [teamRes, instinctsRes, suggestionRes, consensusRes, checklistRes, roadmapRes, attentionRes, themesRes] =
          await Promise.all([
            fetch('/api/team'),
            fetch('/api/team/instincts'),
            fetch('/api/team/suggestion'),
            fetch('/api/team/consensus'),
            fetch('/api/team/checklist'),
            fetch('/api/team/roadmap/check-in'),
            fetch('/api/creators/attention'),
            fetch('/api/creators/themes'),
          ])
        if (!teamRes.ok || !instinctsRes.ok || !suggestionRes.ok) {
          throw new Error('Request failed')
        }
        const [teamData, instinctsData, suggestionData] = await Promise.all([
          teamRes.json(),
          instinctsRes.json(),
          suggestionRes.json(),
        ])
        if (cancelled) return
        setTeam(teamData)
        setInstincts(instinctsData)
        setSuggestion(suggestionData)
        // Deliberately outside the throw above: consensus and the checklist
        // are additive context, so a failure in either leaves that panel
        // absent rather than replacing the whole dashboard with an error.
        if (consensusRes.ok) setConsensus(await consensusRes.json())
        if (checklistRes.ok) setChecklist(await checklistRes.json())
        if (roadmapRes.ok) setCheckIn(await roadmapRes.json())
        if (attentionRes.ok) setAttention(await attentionRes.json())
        if (themesRes.ok) setThemes(await themesRes.json())
      } catch {
        if (!cancelled) {
          setError("Couldn't load your team. Check the connection and try again.")
        }
      }
    }

    load()
    return () => {
      cancelled = true
    }
  }, [])

  if (error) {
    return (
      <div className="error-panel">
        <span className="tag">Error</span>
        {error}
      </div>
    )
  }

  if (!team || !instincts || suggestion === undefined) {
    return <div className="loading-note">Loading your squad…</div>
  }

  return (
    <div className="dashboard">
      {/* First on the screen deliberately: the deadline is the only thing
          here with a clock on it. */}
      {checklist && <DeadlinePanel checklist={checklist} />}

      {suggestion === null ? (
        <div className="loading-note">
          No suggestion yet — run ingestion to get this gameweek&rsquo;s first read.
        </div>
      ) : isTransferPlan(suggestion) ? (
        <TransferPlanCard plan={suggestion} />
      ) : (
        <RecommendationCard recommendation={suggestion} />
      )}

      {checkIn && <RoadmapPanel checkIn={checkIn} />}

      <SquadList team={team} />
      <InstinctPanel notes={instincts.notes} />
      <ConsensusPanel items={consensus} />
      {attention && <AttentionPanel board={attention} />}
      {themes && <ThemesPanel board={themes} />}
    </div>
  )
}
