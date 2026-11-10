# 11 — Transfer decisions + pushback

**What to build:** A `recommend_transfers` tool weighing the cost of a hit against expected gain, using Understat stats (ticket 05), creator evidence (ticket 06), and injury/risk data (ticket 07) as its inputs. "Should I make a transfer?" and "should I roll?" both work. "Talk me out of my current transfer" makes the AI genuinely push back with evidence when the data doesn't support the move, rather than agreeing by default.

**Blocked by:** 05 — Understat stats + get_player/compare_players, 06 — Creator ingestion + extraction + trust tiers, 07 — Injury/risk summary

**Status:** done

- [x] `recommend_transfers` weighs hit cost against expected gain using stats, creator evidence, and risk data together
- [x] Asking "should I make a transfer?" returns a Recommendation with confidence, evidence, risks, and an alternative
- [x] Asking "should I roll?" returns a clear answer on whether banking the transfer is worth it this week
- [x] Given a transfer the evidence doesn't support, "talk me out of my current transfer" produces genuine pushback citing that evidence, not agreement
- [x] The primary pick (transfer or hold) is identical across repeated calls against identical data (ADR-0004)

## Comments

**2026-08-18:** `recommend_transfers(conn, player_out_name, player_in_name, is_hit)` follows the established composable-tool pattern (same as `compare_players`/`predict_price_changes`) rather than building a candidate-search engine: Claude names a specific out/in pair (from squad knowledge via `get_my_team`, creator buy signals, etc.) and the tool returns a deterministic verdict for that pair, not an auto-suggestion across the whole pool — the ticket doesn't ask for candidate discovery, and it would have been scope creep.

`gain_signal` (xG + xA + form combined) is deliberately NOT called `expected_gain` — those are different units (Understat season-cumulative xG/xA vs. FPL's recent-form average) that don't actually compose into a points figure. The tool description tells Claude it's a directional signal only and never to state it as an exact points projection. A `data_complete` flag (false when either player is missing xg/xa/form) exists specifically because the DB is *currently* in exactly that state pre-season — without it, `(xg or 0) + (xa or 0) + (form or 0)` would silently read as a confident zero-gain signal instead of "no data," which would have been actively misleading in the one state the tool is most likely to be called in right now.

"Should I roll?" has no separate code path — `pick` reuses the existing player-centric schema (ADR-0004 predates this ticket and wasn't going to be reworked for it): `pick` = the incoming player if the move is worth it, or the outgoing/current player if not (i.e. "keep this player" = roll). Because that mapping is ambiguous from the response alone, the tool description *requires* the evidence/risks text to say plainly which one it means, and requires a literal `'PUSHBACK:'` tag on a risk specifically when `net_gain_signal` is negative with complete data — otherwise AC3/AC4 ("a clear answer," "genuine pushback") would be unverifiable and dependent on Claude's prose happening to be unambiguous.

`_record_known_players` (`reasoning/claude.py`) gained a `TransferComparison` branch registering both `player_out`/`player_in` — the same known-players gap issue 08 hit (memory-documented). Caught proactively this time, and the integration test (below) specifically excludes `get_player`/`compare_players` from its tools list so the in-candidate is reachable *only* through `recommend_transfers`, which is what would have failed without the branch.

Extracted `get_injury_news.py`'s next-fixture SQL into a shared `fixtures.py::next_fixture_by_club`, reused by both tools rather than duplicated — a natural side-effect of `recommend_transfers` needing the same per-club fixture lookup for both players in a comparison.

Verified at three layers. Unit (`test_recommend_transfers.py`): gain-signal/hit-cost arithmetic, `data_complete` false on missing stats, injury/fixture-risk flagging, creator-opinion inclusion, tool wiring — 7 tests. Integration (`test_recommendations_integration.py`): `test_recommend_transfers_is_the_only_way_to_reach_a_player_it_surfaces` (the known-players regression test described above), `test_claude_pushes_back_on_a_transfer_the_data_does_not_support` (asserts the literal `PUSHBACK:` tag and that the pick keeps the stronger player), `test_endpoint_repeated_calls_return_the_identical_transfer_pick` (ADR-0004, same shape as the existing captain-pick determinism test) — all 3 passed on the first run. Then a live HTTP smoke test against the running dev server: "I'm thinking of transferring Smoke Strong for Smoke Weak, and it would cost me a hit. Talk me out of it if the data doesn't support it." — real Claude called `recommend_transfers`, cited the actual `net_gain_signal` value (-20.3) as "real evidence against the move, not missing data" (correctly distinguishing it from the `data_complete`-false case), included the `PUSHBACK:` tag, and recommended keeping Smoke Strong. Fabricated `fpl_players` test rows and demo team-1 data cleaned up afterward. Full non-integration suite: 98 passed. Full integration suite: 13 passed + 1 previously-seen turn-budget flake (unrelated to this ticket, confirmed passing in isolation).
