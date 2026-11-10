import { useEffect, useState } from 'react'
import type { Instincts, Suggestion, Team } from '../types'
import { isTransferPlan } from '../types'
import InstinctPanel from './InstinctPanel'
import RecommendationCard from './RecommendationCard'
import SquadList from './SquadList'
import TransferPlanCard from './TransferPlanCard'

export default function TeamDashboard() {
  const [team, setTeam] = useState<Team | null>(null)
  const [instincts, setInstincts] = useState<Instincts | null>(null)
  // undefined = still loading, null = loaded but nothing generated yet.
  const [suggestion, setSuggestion] = useState<Suggestion | undefined>(undefined)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false

    async function load() {
      try {
        const [teamRes, instinctsRes, suggestionRes] = await Promise.all([
          fetch('/api/team'),
          fetch('/api/team/instincts'),
          fetch('/api/team/suggestion'),
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
      {suggestion === null ? (
        <div className="loading-note">
          No suggestion yet — run ingestion to get this gameweek&rsquo;s first read.
        </div>
      ) : isTransferPlan(suggestion) ? (
        <TransferPlanCard plan={suggestion} />
      ) : (
        <RecommendationCard recommendation={suggestion} />
      )}

      <SquadList team={team} />
      <InstinctPanel notes={instincts.notes} />
    </div>
  )
}
