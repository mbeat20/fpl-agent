import type { Theme, ThemeBoard } from '../types'
import { timestampLabel } from '../types'

/**
 * Issue 29: what creators are collectively talking about.
 *
 * Every word here is either a count, a term matched literally in a creator's
 * own words, or a quotation. Nothing is written for the reader — a generated
 * summary would be exactly the hallucination surface ADR-0014 closed for
 * citations: plausible, unverifiable, and wrong in a way that looks
 * authoritative.
 *
 * A theme is headed by its `title` and evidenced by its `label`. Those used to
 * be the same string, and a transcript sentence turned out to be unusable as a
 * heading: opened in a browser on 2026-09-04 the panel's themes were called
 * things like "Whereas with now, right, you have this high floor created for
 * you by the variables discussed beforehand." The quote is still here, one
 * line down, where evidence belongs.
 */
export default function ThemesPanel({ board }: { board: ThemeBoard }) {
  if (board.themes.length === 0) return null

  return (
    <div className="panel">
      <div className="panel-head">
        <div>
          <div className="panel-kicker">Common themes</div>
          <div className="pick-name">
            {board.planning_gameweek !== null
              ? `GW${board.planning_gameweek}`
              : 'Across your creators'}
          </div>
        </div>
        <span className="attention-slice">{board.themes.length} clusters</span>
      </div>

      <div className="panel-divider" />

      <div className="themes-list">
        {board.themes.map((theme, i) => (
          <ThemeRow key={i} theme={theme} />
        ))}
      </div>

      <div className="consensus-footnote">
        Grouped by what different creators independently say close to each other. Each
        title is assembled from counted FPL terms and the players the cluster's stances
        resolved to; each quote is one of their actual sentences. Neither is a summary
        written for you.
      </div>
    </div>
  )
}

function ThemeRow({ theme }: { theme: Theme }) {
  const stamp = theme.citation ? timestampLabel(theme.citation.start_seconds) : null

  return (
    <div className="theme-row">
      <div className="theme-head">
        {/* Assembled from counted terms and resolved player names, never
            written. See models.Theme. */}
        <span className="theme-title">{theme.title || theme.label}</span>
        {theme.is_new && <span className="attention-fresh">new</span>}
      </div>

      <div className="theme-head theme-counts">
        <span className="theme-count">
          {theme.creators}
          <span className="consensus-of"> creators</span>
        </span>
        <span className="theme-mentions">{theme.mentions} mentions</span>
      </div>

      {/* A quotation, so it is marked as one — and now clearly evidence for
          the heading rather than standing in for it. */}
      <blockquote className="theme-quote">{theme.label}</blockquote>

      {theme.citation && (
        <a className="citation" href={theme.citation.url} target="_blank" rel="noreferrer">
          {theme.citation.creator_name}
          {stamp && <span className="citation-stamp">{stamp}</span>}
        </a>
      )}

      {theme.players.length > 0 && (
        <div className="theme-players">
          <span className="theme-players-kicker">Players named</span>
          {theme.players.join(', ')}
        </div>
      )}
      {/* The creators themselves, so "4 creators" is checkable rather than a
          bare number. */}
      {theme.creator_names.length > 0 && (
        <div className="theme-players">
          <span className="theme-players-kicker">Creators</span>
          {theme.creator_names.join(', ')}
        </div>
      )}
    </div>
  )
}
