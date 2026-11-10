# 07 — Injury/risk summary

**What to build:** A `get_injury_news` tool surfacing injury, rotation, and fixture risk across the user's whole squad, sourced from the FPL API's own status flags (ticket 03) — no dedicated injury-news source. "What are the risks this week?" returns a squad-wide risk summary.

**Blocked by:** 03 — Real FPL API ingestion

**Status:** done

- [x] `get_injury_news` returns injury/doubt/suspension status for every player in the user's squad
- [x] The tool surfaces fixture-driven rotation risk (e.g. upcoming fixture congestion) using ingested fixture data
- [x] Asking "what are the risks this week?" returns a summary covering the whole squad, not just one player

## Comments

Built `get_injury_news` (`src/fpl_coach/tools/get_injury_news.py`): reads squad status/news (ticket 03's flags) and, per club, the next ingested fixture via a union-and-DISTINCT-ON query, flagging `fixture_risk` on a high-difficulty (FDR ≥ 4) fixture, a missing fixture (blank gameweek), *or* a fixture whose difficulty rating is null — `schema.sql` declares both difficulty columns nullable, and the first version of this crashed with `TypeError: '>=' not supported between NoneType and int` on that case. Wired into `/recommendations` alongside the existing tools. Unit tests in `tests/test_get_injury_news.py` cover all three risk paths plus the crash case.

First pass at the tests scoped fixture cleanup only to the rows they inserted, but `get_injury_news`'s query scans the *whole* `fixtures` table per club — any other row for the same club (e.g. real ingested data from ticket 03, since `.env` points at the live Supabase project) would have silently won the per-club lookup and falsified the assertions. Fixed by scoping setup/teardown to `home_team = ANY(test_clubs) OR away_team = ANY(test_clubs)`, matching `test_ingestion.py`'s wholesale-delete convention.

Criterion 3 needs the real Claude-backed Reasoning implementation to choose to call `get_injury_news` and actually summarize the whole squad, not one player — same situation as issue 05's unverified criterion. Added `test_claude_summarizes_risk_across_the_whole_squad_not_just_one_player` in `tests/test_recommendations_integration.py`; it needs `ANTHROPIC_API_KEY` (unset in `.env`) to run. Run `uv run pytest -m integration -k whole_squad` once a key is added, confirm it passes, and tick the box.

**Resolved (2026-08-17):** With `ANTHROPIC_API_KEY` supplied, this test initially failed on two counts. First, two bugs shared with issues 04/05 in `reasoning/claude.py`'s call path: a `temperature=0` param `claude-sonnet-5` now rejects (removed), and Claude ending a turn with plain text instead of a tool call (fixed with `tool_choice={"type": "any"}`). After those, Claude's actual risk summary correctly covered all three squad players — but the test's own assertion only matched full names (`"Mohamed Salah"`) against prose that naturally used last names (`"Salah and Saka have no reported injury issues"`), so it read as a failure that wasn't one. Fixed the assertion in `tests/test_recommendations_integration.py` to match last names, consistent with the existing `"Haaland" in risk` check right below it. Test passes; full suite (38 unit + 5 integration) green.
