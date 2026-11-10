import type { AttentionBoard, ThemeBoard } from '../types'
import { useEndpoint } from '../useEndpoint'
import AttentionPanel from '../components/AttentionPanel'
import ThemesPanel from '../components/ThemesPanel'

/**
 * What trusted creators are talking about.
 *
 * Its own page because it is not about the user's squad. `/creators/attention`
 * says so in its own docstring -- a player they do not own is exactly the
 * interesting row -- and it spent its whole life so far filed under a tab
 * called "My Team", which is the wrong promise about what it is for.
 *
 * The framing that matters is carried at the page level rather than repeated
 * per row (see AttentionPanel): heavy or fast-moving coverage is a REASON TO
 * CHECK your reasoning, not confirmation of it. preferences.md treats it as a
 * risk, and ticket 19 deliberately refused to build a buy list.
 */
export default function DiscoursePage() {
  const attention = useEndpoint<AttentionBoard>('/api/creators/attention')
  const themes = useEndpoint<ThemeBoard>('/api/creators/themes')

  return (
    <div className="dashboard">
      <div className="page-lede">
        Movement in what creators are saying, counted per creator rather than per post. A count
        that jumped this week and a settled majority look identical as numbers and mean
        opposite things — so the movement is always shown next to the level, and neither is a
        reason to buy anyone.
      </div>

      {attention.loading && <div className="loading-note">Counting the discourse…</div>}
      {attention.data && <AttentionPanel board={attention.data} />}

      {themes.loading && <div className="loading-note">Clustering what they said…</div>}
      {themes.data && <ThemesPanel board={themes.data} />}

      {attention.failed && themes.failed && (
        <div className="panel panel-note">
          Couldn&rsquo;t load either creator board. If creator ingestion has never run, there
          is nothing to show yet.
        </div>
      )}
    </div>
  )
}
