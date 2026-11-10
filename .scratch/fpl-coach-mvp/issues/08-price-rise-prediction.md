# 08 — Price-rise prediction

**What to build:** A `predict_price_changes` tool using ingested ownership/transfer-trend data (ticket 03) to flag players likely to rise or fall in price before it happens. "Which players are expected to rise?" returns a ranked list.

**Blocked by:** 03 — Real FPL API ingestion

**Status:** done

- [x] `predict_price_changes` returns players likely to rise or fall, derived from ingested ownership/transfer-trend data
- [x] Asking "which players are expected to rise?" returns a ranked list with the evidence behind each flagged player

## Comments

**2026-08-18:** `bootstrap-static` doesn't carry a "price rise probability" field — FPL's own threshold is undocumented. Added `transfers_in_event`, `transfers_out_event`, `cost_change_event` columns to `fpl_players` (schema.sql + `pipeline.py`) and built `predict_price_changes` on a momentum score: net transfers this gameweek divided by (ownership % + 1). Raw net-transfer counts alone would bury low-ownership risers/fallers under high-ownership noise, since a low-ownership player needs far fewer real transfers to move price than a heavily-owned one — the ownership-relative score approximates that dynamic. `cost_change_event` is surfaced in the evidence string ("price has already moved today") when nonzero, since that means today's update already happened rather than being an upcoming prediction.

Verified with real, non-zero data at every layer: unit tests (`test_predict_price_changes.py`, hand-seeded `fpl_players` rows) prove the ownership-relative ranking, the falling/rising split, exclusion of zero-net-movement players, and the `cost_change_event` evidence note. A Seam-2 integration test (`test_claude_flags_the_price_riser_with_evidence`) proves real Claude actually calls `predict_price_changes` and cites the net-transfer evidence rather than defaulting to a squad pick. Then smoke-tested over live HTTP against the running dev server with two hand-seeded pool players (one clear low-ownership riser, one flat) — asked "Which players are expected to rise in price?" and got back a Recommendation correctly picking the riser and citing "Net +24,700 transfers in despite just 1.5% ownership" as evidence; cleaned up the seed rows afterward since they were fabricated, unlike issue 06's real `@FPLGeneral` creator.

**Real bug found and fixed in the process, not scoped to this ticket:** `reasoning/claude.py`'s `_record_known_players` only widened `submit_recommendation`'s valid-pick-id enum for `Player`/`Team`/`PlayerComparison` tool results — a `PriceChangePredictions` result wasn't recognized at all, so Claude would have been unable to ever submit a pick returned by `predict_price_changes` (not in the enum → API-level validation error). Generalized `known_players` to a small `_KnownPlayer(id, name)` shape and taught `_record_known_players` to unpack `PriceChangePredictions.rising`/`falling` into it. Full non-integration (62 passed) and integration (8 passed) suites confirm this didn't regress issues 04/05/07's real-Claude tests.

**Also found and fixed:** the FakeGrokClient in `test_creator_pipeline.py` (issue 06) was being handed to `run_creator_ingestion` with no creator scoping, so it processed *every* creator in the shared database — including the real `@FPLGeneral` creator — and wrote fake test post URLs into its real `creator_content` rows on every test run. Added an optional `creator_ids` filter to `run_creator_ingestion` and scoped the three affected tests to their own freshly-inserted test creator. Cleaned up the two polluted rows that had already leaked into `@FPLGeneral` from earlier runs this session.
