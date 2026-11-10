# A Creator has platform Sources, and transcript fetching is rate-limited by YouTube, not by us

Ticket 06 modelled a Creator as an X account: `creators.x_handle` was `NOT NULL UNIQUE` and was the identity. That held only as long as X was the sole source. Ticket 16 makes YouTube transcripts the richest input by a wide margin — one measured transcript is ~6,000 verbatim words against the ~115-word Grok *paraphrase* an X post yields — and several of the best FPL creators publish video with no X presence worth ingesting.

So platform identity moves into `creator_sources (creator_id, platform, external_id)`, and `x_handle` becomes nullable rather than being migrated away. Two consequences worth stating, because both look like accidents otherwise:

- **`external_id` is the platform's own id, not the handle.** For YouTube that is the `UC…` channel id, resolved from the `@handle` once at add-time. Handles get renamed; ids do not. It also matters that the YouTube handle is *not* the X handle — `@LetsTalk_FPL` on X is `@LetsTalkFPL` on YouTube — so deriving one from the other per run would silently resolve to the wrong channel or none at all.
- **`add_creator` looks the existing row up explicitly instead of using `ON CONFLICT`.** A single conflict target cannot serve both cases: `ON CONFLICT (x_handle)` never fires for `NULL`, so a handle-less creator would be inserted on every re-run, while keying on `name` would break ticket 06's rename-by-handle. Worse, giving a handle-less creator an X handle later conflicts on `name` while the statement targets `x_handle` — a plain `UniqueViolation`. One extra `SELECT` buys correctness in all four combinations.

## Rate limiting is a hard external constraint, not a tuning knob

Measured 2026-08-27: 24 concurrent transcript fetches got this machine's IP blocked by YouTube outright, and the block did not lift within the hour. A single fetch had worked fine days earlier, so the limit is on request *volume*, not on access.

The design therefore treats "blocked" and "no captions" as genuinely different outcomes, because the correct responses are opposite:

- No captions on this video → skip it, keep going. It is counted, not raised.
- Blocked → stop the transcript leg for the whole run. Every further request extends the ban. Whatever was already committed stands, and the next run resumes from there because idempotency is keyed on `(creator_id, source_url)`.

Fetches are serialised with a delay between them, and each creator has a per-run video cap. `YOUTUBE_PROXY_URL` is supported for volume beyond one run a day. Two things follow that are easy to mistake for oversights: the Atom feed only ever returns a channel's latest 15 videos with no paging, so **ingestion must run at least weekly or videos are lost permanently**; and the per-creator cap means a long gap loses the *oldest* videos in the window, not the freshest.

## Transcript text is stored with its `[MM:SS]` markers inline

The obvious alternative — store clean prose, keep timings in a side table — was rejected. Embedding is deliberately driven off "content rows that lack chunks" rather than off what this run ingested, so it backfills rows written before the current code existed, and that backfill only ever reads `content_text`. Stripping the markers at write time would make every already-stored transcript's timestamps unrecoverable, and timestamps are the whole basis of citable deep links (`youtu.be/<id>?t=743`). The markers cost a few percent of tokens and are removed from each chunk's text as it is split.

## The stance vocabulary widened because the schema was discarding the best claims

`player_name NOT NULL` plus a `buy|sell|hold|captain` enum could not store a transcript's most valuable statements: *"wildcard around GW8"*, *"Arsenal's run from GW6 is the best in the game"*. Extraction was silently throwing them away. `player_name` is now nullable with a `subject` alongside it, and the vocabulary adds `avoid`, `differential`, `bench`, `chip_timing`, `fixture_view`.

This does not weaken ADR-0004. A pick's `pick_player_id` enum is built only from tool results that carry a real `Player`, and no creator-opinion result carries one — so a `chip_timing` Stance about "wildcard" has no path to becoming a recommended player. A Stance with neither a player nor a subject is dropped rather than stored, because an unattributable opinion would still be counted in a consensus.
