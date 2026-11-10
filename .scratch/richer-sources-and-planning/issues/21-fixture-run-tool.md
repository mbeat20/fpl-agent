# 21 — Fixture-run tool: multi-gameweek FDR, blanks and doubles

**What to build:** A tool that returns a club's fixture *run* over the next N gameweeks — every fixture, its difficulty, home or away — and explicitly flags blank and double gameweeks. Pure SQL over the `fixtures` table, which already holds everything needed.

**Blocked by:** —

**Status:** ready-for-human

## Why

`fixtures.next_fixture_by_club` returns exactly one fixture per club: `SELECT DISTINCT ON (club) ... WHERE kickoff_time > now() ORDER BY club, kickoff_time`. Every consumer — `get_injury_news`, `recommend_transfers`, and therefore every Recommendation and every `TransferPlan` step — sees the football world one gameweek deep.

This is the largest single reason multi-week planning is currently plausible rather than credible. `fpl_rules.py` tells the model:

> A move that is neutral now but sets up a good run of fixtures beats a move that wins this week and forces another next week.

It cannot see a run. Worse, the structural moments that chips exist for are invisible: a **blank** gameweek (a club has no fixture) and a **double** (a club has two) are both erased by `DISTINCT ON`, which silently keeps one row. A plan that plays Bench Boost into a blank is the kind of confidently wrong advice this ticket prevents.

## Approach

- `get_fixture_run(clubs, weeks)` returning per club an ordered list of fixtures across the horizon, each with opponent, home/away, and FDR, plus a per-gameweek count so blanks (0) and doubles (2+) are explicit rather than inferred by the model counting rows.
- Home and away matter and are currently thrown away by the `UNION ALL` flattening in `_NEXT_FIXTURE_SQL` — keep the distinction.
- Aggregate the run (mean/total FDR over the horizon) so runs are comparable between clubs, but return the individual fixtures too. An average alone hides a run that is three easy games then a wall.
- **A blank is not the same as missing data.** `fixtures.gameweek` is nullable (an unscheduled fixture) and a club genuinely having no fixture in a gameweek are different facts, and must not collapse into the same output. `next_fixture_by_club` already returns `None` for both cases; do not repeat that.
- The FPL fixture list changes: cup progression and TV picks create and resolve blanks and doubles mid-season. `run_ingestion` already does `DELETE FROM fixtures` and reinserts wholesale each cycle, so freshness is handled — but a persisted plan built on last month's fixture list will silently rot, which ticket 25 has to deal with.
- Leave `next_fixture_by_club` alone for its existing single-fixture callers; this is an additional capability, not a refactor of ticket 07 and 11's tools.

## Acceptance criteria

- [x] A tool returns a club's fixtures over the next N gameweeks, each with opponent, home/away, and FDR
- [x] A gameweek in which a club has no fixture is explicitly reported as a blank, distinct from "no data"
- [x] A gameweek in which a club has two fixtures is explicitly reported as a double, with both fixtures present
- [x] The run carries an aggregate difficulty for the horizon *and* the individual fixtures, so a lumpy run is not hidden behind an average
- [x] Several clubs can be compared over the same horizon in one call, so the reasoning layer does not burn turns looping one club at a time
- [x] The reasoning layer uses it when asked to compare transfer targets over the next few gameweeks, verified by an integration test asserting the run is cited
- [x] A multi-week `TransferPlan` cites specific gameweeks' fixtures from the run rather than generic fixture prose
- [x] Existing `next_fixture_by_club` callers (tickets 07, 11) are unchanged and still green

## Comments

**Built 2026-08-30.** Six of eight ACs done. The two `[~]` are the integration
ACs: both tests are written, neither has been executed (they cost real Claude
calls).

### The `clubs` table, as STATUS.md asked

`clubs` (FPL team id, name, short_name), upserted by `run_ingestion` from
`bootstrap['teams']` — already fetched, previously discarded except as an
in-memory `clubs_by_id` map that existed only for the duration of a run.

Short name stays the join key everywhere (`fixtures.home_team`,
`players.club`, `fpl_players.club` all store it). The table exists for two
things: resolving a bare **team id** met outside ingestion, and letting a
question say "Arsenal" where the database says "ARS".

**It immediately closed a known gap.** Ticket 20 left `opponent` NULL on the
on-demand minutes path because `element-summary` returns an opponent *team id*
and nothing outside ingestion could resolve it. `refresh_squad_minutes` now
loads the map from `clubs` when a caller does not supply one, so `get_player`
fills it in. Verified live: `get_player("Semenyo")` now returns
`GW2 90 min vs CRY away`, where it previously returned `vs None`.

There was a second half to that bug: `_store`'s `ON CONFLICT DO UPDATE` never
updated `opponent` or `was_home`, so a row first written with a NULL opponent
could not have been repaired even after the map existed. Both are in the SET
now, so the cache self-heals the next time a player's history goes stale.

### The tool

`get_fixture_run(clubs, weeks)`, default 5 (FPL's own ticker convention),
capped at 12. One SQL query answers for many clubs at once — the ticket is
explicit that looping one club per turn would burn the reasoning layer's turn
budget.

- **Home and away survive.** `_NEXT_FIXTURE_SQL`'s `UNION ALL` flattens them
  into one "opponent" column. FPL rates the two sides of the same fixture
  differently, so taking the wrong side's FDR inverts the judgement — the
  query keeps `is_home` and picks the right side's rating.
- **Blanks and doubles are stated, not inferrable.** `is_blank` / `is_double`
  per gameweek, plus `blanks` / `doubles` lists per club, plus a prose `note`
  that names the Bench Boost consequence. A model asked to count rows will
  eventually miscount, and this is the fact chips turn on.
- **`average_difficulty` is per FIXTURE, not per gameweek.** Dividing by
  gameweeks scores a blank as difficulty 0 and makes a club with no fixture
  look like a club with an easy one, which is backwards. `fixtures_count` is
  returned as the honest denominator.
- **A missing FDR is unknown, not easy.** `difficulty_complete` goes false and
  the aggregates cover only rated fixtures. A NULL summed as zero would read
  as the easiest possible fixture.
- **Unscheduled is not blank.** `fixtures.gameweek` is nullable (a fixture
  awaiting a slot). Counted in `unscheduled_fixtures` with its own note.
  `next_fixture_by_club` collapses this with a genuine blank; this does not.
- **The horizon starts at the planning gameweek**, not at "the next fixture in
  the table". They differ in exactly the window where planning happens.
- **Unknown club names are echoed back** in `unknown_clubs`. A club silently
  missing from the output reads as a club with no fixtures, which reads as a
  blank, which is a decision.

`next_fixture_by_club` is untouched, and there is a test asserting so.

### A bug the tests caught

`weeks or DEFAULT_WEEKS` treated an explicit `weeks=0` as unset and silently
returned a five-gameweek horizon. Now `None`-checked and clamped.

### Another shared-database landmine found and defused

`test_ingestion`'s fake bootstrap used **real FPL club ids 1, 2, 3** with the
short names LIV/MCI/ARS. Harmless while nothing persisted a club, but `clubs`
is global — the fake would have upserted a wrong id -> short-name mapping over
the real one, and a *wrong* opponent name written into
`player_gameweek_history` is worse than the NULL it replaced. The fixture now
uses ids 9001-9003 and fake short names (ZLV/ZCI/ZAR) that cannot collide, and
`_cleanup` removes them. Same treatment as `gameweeks` in ticket 19.

### Verification

- `test_get_fixture_run.py`: 13 tests, all passing. Blank, double, unscheduled
  -vs-blank, a club with no fixtures at all, aggregate-and-individual, multi
  -club comparison, full-name resolution, unknown-club echo, missing-FDR,
  horizon start and bounds, and `next_fixture_by_club` unchanged.
- `test_ingestion.py` gained `test_the_club_identity_map_is_ingested`.
- Verified live against the real 380-fixture table: ARS and MCI over GW3-7
  returned correct opponents, home/away and FDR, `"Man City"` resolved by full
  name, and `"Nowhere"` came back in `unknown_clubs`.

### Open

- The two integration tests
  (`test_claude_compares_transfer_targets_on_the_run_and_cites_a_specific_gameweek`,
  `test_a_multi_week_plan_cites_gameweeks_from_the_fixture_run`) build two
  identical forwards whose clubs' runs diverge — same position, price, xG, xA,
  form and minutes, so the run is the only thing that can separate them and a
  wrong pick proves the tool was not consulted. Run with
  `uv run python -m pytest -m integration -k fixture_run`.
- **Ticket 22 will want this**: a step whose player has a blank in that
  gameweek is one of ticket 25's per-step violations, and the blank data now
  exists to check it.

### Integration run, 2026-08-30

Run with `uv run python -m pytest -m integration`. **This ticket's integration
AC(s) passed.** The wider run needed three rounds because the integration file
had rotted while excluded from the default suite — details in ticket 22's
comments, since that is where the fixtures were repaired.

One caveat on the final confirming pass: it ended `20 passed, 3 failed`, and
one of those three is **`anthropic.BadRequestError: Your credit balance is too
low`** — the account ran out of credit mid-run. The other two
(`test_claude_summarizes_risk_across_the_whole_squad_not_just_one_player`,
`test_claude_pushes_back_on_a_transfer_the_data_does_not_support`) both
*passed* in the immediately preceding run and are assertions on free-form model
prose, so they are flaky rather than broken. Every integration test in the file
has passed at least once, but **there has been no single clean green run**, and
credits are exhausted so one could not be obtained. Say that rather than
claiming a green suite.
