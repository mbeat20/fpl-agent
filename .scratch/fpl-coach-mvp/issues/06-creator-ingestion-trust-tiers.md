# 06 — Creator ingestion + extraction + trust tiers

**What to build:** Trusted X creator posts ingested via Grok's Live Search API (not a direct X API subscription). Each piece of raw content is run through an LLM extraction step (Layer 2) into one or more structured Creator Stance records (player, buy/sell/hold/captain, confidence, reason, creator), stored per ADR-0005 — structured fields in Postgres, source raw text chunked and embedded into pgvector (via Voyage AI) and linked back by reference. Each creator is manually assigned a trust tier (High/Medium/Low, ADR-0001) at add-time. "What do trusted creators think about [player]?" returns a synthesis weighted by trust tier.

**Blocked by:** 03 — Real FPL API ingestion

**Status:** done

- [x] A creator can be added with a manually assigned trust tier (High/Medium/Low)
- [x] Raw X posts from added creators are ingested via Grok's Live Search API
- [x] An LLM extraction step converts raw content into one or more Creator Stance records (a single piece of content may yield multiple Stances)
- [x] Each Creator Stance's structured fields are stored in Postgres; its source raw text is embedded into pgvector via Voyage AI and linked back by reference
- [x] Asking "what do trusted creators think about [player]?" returns a view synthesized across creators, weighted by trust tier
- [x] Ingestion tests fake the Grok client at its HTTP boundary with canned fixture responses; extraction is tested against known raw-text inputs and expected structured Stance output

## Comments

**2026-08-18:** Built with `XAI_API_KEY` supplied by the user. Real API shape verified via two live calls before writing `GrokClient` — xAI's *current* X Search tool (`POST /v1/responses`, `tools: [{"type": "x_search", ...}]`) replaced the old `search_parameters` Live Search API, which was retired 2026-01-12. Important finding that shaped the schema: the response is a synthesized paraphrase of cited posts, not verbatim tweet text, with per-bullet `url_citation` annotations rather than a flat citation list — `creator_content.content_text` is documented as paraphrase, not verbatim, to avoid future confusion.

Design decisions from the pitfalls call before building:
- **No FK from `creator_stances`/`creator_content` to `fpl_players`** — best-effort match by name into `fpl_player_id`, NULL when unmatched. Same convention as `players.fpl_element_id`; required because ingestion does a wholesale `DELETE FROM fpl_players` on every refresh, which would break/cascade a hard FK.
- **Idempotent re-ingestion** via a `UNIQUE` constraint on `creator_content` + `ON CONFLICT DO NOTHING RETURNING id`, checking for a `None` row to detect "already seen" and skip re-extraction. Tested explicitly (`test_re_running_ingestion_is_idempotent_and_does_not_duplicate`).
- **Real-extractor test, not just a canned fake** — a `tool_use` fake only proves plumbing works, not that extraction produces correct fields from real text. `test_extraction.py` has two `@pytest.mark.integration` tests hitting the real `ClaudeStanceExtractor` against fixed input with known expected buy/sell stances.

Criterion 5 (trust-weighted synthesis view) verified two ways: unit tests against hand-seeded stances (`test_get_creator_opinions.py`, proves sort-by-trust-tier ordering independent of Grok/Voyage), and a live end-to-end smoke test — added a real creator (`@FPLGeneral`, high trust), ran `fpl-ingest-creators` for real (5 posts, 7 stances extracted), then asked `/recommendations` "What do trusted creators think about Sabitzer as a captain option?" over HTTP. Claude called `get_creator_opinions`, correctly cited "FPL General (high trust tier)" with the actual extracted stance and confidence, and flagged the single-source risk. Confirms the tool is wired into `routes.py` and Claude actually reaches for it, not just that it's reachable.

**2026-08-18 (criterion 4, `VOYAGE_API_KEY` now supplied):** Verified the key live before writing any code — `voyage-3` returns 1024-dim embeddings, confirmed with a one-off script hitting `POST /v1/embeddings` directly. Also confirmed pgvector was already enabled on the Supabase DB (`public` schema) before adding `content_chunks vector(1024)` to `schema.sql`, so no extension-schema surprises.

`VoyageClient` (`ingestion/voyage_client.py`) wraps the embeddings endpoint on `httpx` (not the `voyageai` SDK), same HTTP-boundary-fake convention as `GrokClient` — keeps this ticket's own AC6 ("fake the client at its HTTP boundary with canned fixture responses") intact for `test_voyage_client.py`. psycopg has no built-in `vector` adapter; rather than add the `pgvector` package, embeddings are passed as a `"[0.1,0.2,...]"` string literal and cast with `%s::vector` (`voyage_client.vector_literal`, shared by the pipeline and the ticket 09 search tool).

Embedding is driven off *content lacking a `content_chunks` row* (`_embed_pending_content` in `creator_pipeline.py`), not off `content_ingested` from the current run — the naive version (embed-only-what-this-run-just-ingested) would have permanently skipped the 5 real `@FPLGeneral` posts already sitting in the live DB from this ticket's earlier structured-storage work, leaving ticket 09 with almost nothing to search. Driving it off "lacking chunks" backfills existing content for free and stays idempotent on re-run. Scoped by the same `creator_ids` as the rest of the run (never an unscoped "any creator lacking chunks" query) — an unscoped version would let a test's `_FakeVoyageClient` write fabricated vectors into a real creator's `content_chunks` rows, the same shared-DB pollution class as issue 08's `creator_ids` fix and issue 13's `gameweek_state` keying (see [[project_shared_db_state_scoping]]).

Verified three ways: unit tests (`test_voyage_client.py`'s HTTP-boundary fake; `test_creator_pipeline.py`'s `_FakeVoyageClient` proving chunk counts and idempotency), a `@pytest.mark.integration` test hitting the real Voyage API and asserting the 1024-dim contract schema.sql assumes, then a live run of `fpl-ingest-creators` against the real `@FPLGeneral` creator — backfilled all 5 pre-existing posts plus 1 newly-ingested post, `content_chunks` count landed at 6, matching `creator_content`'s count exactly.

Unrelated side note surfaced while testing: `test_claude_looks_up_and_cites_stats_for_a_player_outside_the_squad` (issue 05, marked done) is flaky against the real Claude API — it passed once during that ticket's original resolution (turn budget raised to 10, `tool_choice: {"type": "any"}` added) but failed on a re-run today with the same `RuntimeError: Claude did not call submit_recommendation within the turn budget` for the Ollie Watkins question. Not touched here to stay in scope for this ticket; flagged on issue 05 instead.
