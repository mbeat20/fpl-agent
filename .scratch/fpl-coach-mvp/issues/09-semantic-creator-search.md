# 09 — Semantic creator-opinion search

**What to build:** A `search_creator_opinions` tool performing pgvector similarity search over the embedded Creator Stance source text (ticket 06), enabling loose semantic queries beyond exact player-name lookup — e.g. "find opinions about Newcastle's fixture run."

**Blocked by:** 06 — Creator ingestion + extraction + trust tiers

**Status:** done

- [x] `search_creator_opinions` performs a pgvector similarity search over embedded Creator Stance source text
- [x] A semantic query with no exact player name (e.g. about a team's fixture run) returns relevant Creator Stances
- [x] Each result resolves back to its Creator Stance's structured fields (creator, trust tier, confidence), not just raw text

## Comments

**2026-08-18:** `search_creator_opinions` (`tools/search_creator_opinions.py`) embeds the query via `VoyageClient`, then does a two-stage SQL query: a CTE narrows `content_chunks` to the nearest N by cosine distance (pgvector's `<=>` operator), then joins out to `creator_stances`/`creators` and takes the top `limit`. Two-stage rather than a flat join+`ORDER BY`+`LIMIT`: a flat version lets one verbose post's several Stances crowd out every other creator in the results, since the `LIMIT` would apply after the fan-out rather than before it. A `content_chunks` row whose `content_id` has no `creator_stances` row (ADR-0005: a content row can yield zero Stances) is dropped by the inner join rather than surfaced as bare unresolved text — required by AC3.

Distinct from `get_creator_opinions` (ticket 06): that tool is an exact-ish `ILIKE` match on `player_name`; this one has no name filter at all and relies entirely on embedding proximity, which is what makes AC2 (a query with no exact player name) work. Both tools are registered — `search_creator_opinions`'s description tells Claude to prefer `get_creator_opinions` for a single named player and reach for this one for loose/topical queries.

`SemanticCreatorMatch` results deliberately do NOT feed `_record_known_players` in `reasoning/claude.py` — same reasoning as issue 13's `Instinct`: it's evidence, not a structured player pick, so it shouldn't widen `submit_recommendation`'s valid-pick enum.

Verified at three layers. Unit (`test_search_creator_opinions.py`): hand-seeded `content_chunks` with near-orthogonal unit vectors (axis-aligned, cosine distance exactly 0 to themselves and 1 to every other axis) so nearest-neighbour ordering is provable with zero API calls, same idea as `_FakeGrokClient`/`FakeStanceExtractor`'s canned fakes elsewhere — 4 tests covering ranking, AC2 (no-exact-name query), AC3 (stance-less chunk exclusion), and tool-handler wiring. Integration (`test_recommendations_integration.py::test_claude_uses_semantic_search_for_a_query_with_no_exact_player_name`): seeds one real creator/content/stance row, embeds it with a *real* Voyage call (a fake axis-aligned vector would make cosine similarity against a real live query embedding meaningless), asks real Claude "What are creators saying about Newcastle's fixture run?" with no player name in the question, and asserts it quotes a distinctive tag planted in the Stance's `reason` — passes, confirming Claude reaches for `search_creator_opinions` on a loose query and the embedding space is consistent between seed-time and query-time. Then a live HTTP smoke test against the running dev server, real `@FPLGeneral` data (6 embedded posts from ticket 06's backfill): asked "What are trusted creators saying about upcoming fixture difficulty for defenders?" — Claude called the tool across several rephrased queries, correctly cited "FPL General (high trust tier)" with an actual quoted post, and (correctly, since the real content didn't substantively cover the topic) reported the data was thin rather than fabricating a signal. First attempt hit the pre-existing, previously-flagged (issue 06 comments) `RuntimeError: Claude did not call submit_recommendation within the turn budget` flakiness unrelated to this ticket; the retry succeeded.
