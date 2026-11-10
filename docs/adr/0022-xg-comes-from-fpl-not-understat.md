# xG/xA come from FPL's own API, not Understat

Issue 05 pulled season xG/xA from Understat by POSTing to the endpoint its own league page uses, then matched each row to an FPL player **by full name**. On 2026-11-06 that broke twice over:

- **Understat went down.** Every URL served a "We will come back soon" maintenance page, and the stats endpoint returned `405 Not Allowed`. Because the pipeline treated Understat as required, the whole FPL refresh failed with it — squad, fixtures, outcomes and all.
- **The name match was silently dropping regulars.** 117 pool players with minutes had no xG at all, among them Gabriel, Rúben Dias, Alisson and Igor Thiago (FPL full name `Igor Thiago Nascimento Rodrigues`; Understat `Igor Thiago`). `recommend_transfers` then reported `data_complete: false` and a gain signal that scored the missing figures as zero — Thiago, fifth in the league for xG, came out as a −4.92 transfer target.

## Decision

Read `expected_goals` and `expected_assists` from the `bootstrap-static` elements the pipeline already fetches. FPL has published Opta's figures there since 2022/23, keyed by player id. `UnderstatClient`, `UNDERSTAT_SEASON` and the season-fallback logic are deleted.

## Consequences

- No name matching for xG at all, so the coverage gap goes away: every pool player has a figure. ADR-0020's argument is about creator stances; this sidesteps the problem instead of tuning a matcher.
- One fewer outside request per refresh, and no dependency on an unofficial endpoint that can vanish.
- The numbers change. Opta and Understat model chances differently. xG mostly agrees within a few tenths, while xA can differ substantially (Haaland 1.74 → 0.85, Saka 0.45 → 1.20 at the time of the switch). Signals computed before and after the switch are not directly comparable.
- An absent field stays NULL, not 0, so "unknown" is never scored as "nothing".
- Not taken yet: FPL also publishes `expected_goals_conceded` and per-90 rates for each. They would need new `fpl_players` columns in `schema.sql`.
