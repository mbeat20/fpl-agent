# 16 — Multi-platform creator sources + YouTube transcript ingestion

**What to build:** A creator becomes something that can be followed on more than one platform, and YouTube videos become a first-class content source ingested as full, timestamped, verbatim transcripts — not paraphrases. This replaces the ~115-word Grok paraphrase as the richest input to Layer-2 extraction, and gives every later ticket in this phase something worth reasoning over.

**Blocked by:** —

**Status:** ready-for-human

## Why this first

`creator_content.content_text` today averages 115 words and is Grok's own paraphrase of a cited post, not the creator's words (documented in `schema.sql`, confirmed live during ticket 06). A single Let's Talk FPL video transcript measured **6,378 words across 936 timestamped snippets** — ~55× the information, verbatim, with timestamps that resolve to citable deep links. Only Let's Talk FPL was actually probed, and its feed carried a same-day GW2 transfer-tips video — how many of the other eleven publish video, and how often, is **unverified**. Resolving that is the first step of this ticket, not an assumption to build on.

## Verified approach (probed 2026-08-27 — do not re-derive)

- **Channel discovery:** fetch `https://www.youtube.com/@<handle>` and read `channel_id=UC...` out of the HTML. Store it; do not resolve it on every run.
- **Video listing:** `https://www.youtube.com/feeds/videos.xml?channel_id=<UC...>` → HTTP 200, latest 15 videos with `yt:videoId`, `title`, `published`. No API key, no quota, no YouTube Data API. The `?user=` form is dead (404).
- **Transcripts:** use `youtube-transcript-api`. Confirmed working from this machine. **Do not hand-roll it** — the `captionTracks` `baseUrl` from the watch page returns HTTP 200 with an *empty body* because of YouTube's proof-of-origin hardening, which is a silent failure that looks like "no captions".
- Follow the existing HTTP-boundary-fake convention (`FPLClient`, `GrokClient`, `UnderstatClient`): the client takes its transport so tests fake canned responses rather than mocking the class. `youtube-transcript-api` is not an `httpx` client, so wrap it behind a narrow `TranscriptSource` protocol with a fake, the same shape as `StanceExtractor`.

## Which creators get a YouTube source

**All 12** (user decision, 2026-08-27) — do not cherry-pick a subset up front.

Expect that not all 12 resolve to a channel. `@SolioAnalytics` and `@LiveFPLnet` are data/analytics accounts, and ticket 14's closing comment already records that both contributed content but **zero stances**, since their posts carry no buy/sell framing. A creator with no channel must be a recorded no-op, not an error that aborts the run — report which creators resolved to a channel and which did not, so the roster can be pruned on evidence rather than assumption.

Related: all 12 are currently `trust_tier = 'high'`, and four of them (`@FFScout`, `@fplreview`, `@elevenify`, `@SolioAnalytics`) were chosen by an agent rather than by the user (ticket 14, 2026-08-21). Five more (`@FPL_Architect`, `@FPL_Heisenberg`, `@FPLFocal`, `@LetsTalk_FPL`, `@FPL_JianBatra`) have no recorded provenance at all. That is ticket 18's problem, not this one, but it is why "high trust" should not be read as vetted.

**Roster settled 2026-08-27:** the provenance gap was put to the user and they chose to keep all 12 as-is, undocumented five included. Do not re-litigate the roster in this ticket or in 18 — prune only on measured yield.

## Schema work

- `creator_sources(id, creator_id, platform, external_id, added_at)` with `UNIQUE (platform, external_id)`. `creators.x_handle` stays for now (ticket 06's `fpl-add-creator` keeps working); a migration path off it is not this ticket's job.
- `creator_content` gains `platform`, `published_at`, `title`, `duration_seconds`. **`published_at` is the important one** — today there is only `fetched_at`, so recency ranking of a Stance is impossible (see ticket 18).
- `content_chunks` gains `start_seconds` (nullable — an X post has no timestamp), so a semantic match resolves back to a moment in a video (ticket 17).
- Keep `UNIQUE (creator_id, source_url)` + `ON CONFLICT DO NOTHING` idempotency exactly as ticket 06 established it.

## Chunking and extraction

- `_chunk_text`'s flat 2000-char split has never actually split anything (295 chunks for 290 content rows). It is wrong for 6,000 words: replace with ~500-token overlapping chunks that carry the `start_seconds` of their first snippet.
- Extract **once per transcript with the whole transcript in the call**, not once per chunk — cheaper, and it catches claims that span the video ("as I said about Arsenal earlier").
- `ClaudeStanceExtractor` currently defaults to `REASONING_MODEL` (Sonnet). 12 creators × ~1 video/day × 6k words on Sonnet is not a sensible recurring bill: default extraction to Haiku 4.5 independently of the reasoning model, and cache the tool schema.
- Gate on `published_at` so an already-ingested video is never re-fetched or re-extracted.

## The taxonomy blocker

`creator_stances.player_name` is `NOT NULL` and the stance enum is `buy|sell|hold|captain`. A transcript's most valuable claims cannot be stored at all today: *"wildcard around GW8"*, *"Arsenal's run from GW6 is the best in the game"*, *"he'll be rotated in the cups"*. This ticket must widen that or the extra content is thrown away at the point of extraction. Make `player_name` nullable and extend the vocabulary with at least `avoid`, `differential`, `bench`, `chip_timing`, `fixture_view`.

Note `reasoning/claude.py`'s `_record_known_players` gap ([[project_claude_reasoning_known_players]]): a non-player stance must not be able to reach `submit_recommendation` as a pick.

## Acceptance criteria

- [x] A creator can be given a YouTube source, and its channel id is resolved from the `@handle` once and stored rather than re-resolved per run
- [x] All 12 existing creators are attempted; a creator with no resolvable channel is a recorded no-op rather than a run-aborting error, and the run reports which resolved and which did not
- [x] Ingestion lists a channel's recent videos from the RSS feed with no YouTube API key configured anywhere
- [x] A video's full transcript is stored as `creator_content` with `platform`, `published_at`, `title`, and `duration_seconds` populated
- [x] The transcript is chunked into overlapping ~500-token chunks, each carrying the `start_seconds` it begins at, and embedded into `content_chunks`
- [x] Layer-2 extraction runs once per transcript (not per chunk) and yields multiple Stances from one video
- [x] A claim that names no player (chip timing, a team's fixture run) is stored rather than discarded, and cannot be returned as a `submit_recommendation` pick
- [x] Re-running ingestion ingests and extracts nothing for a video already seen, verified by a test asserting zero new rows on the second run
- [x] Extraction defaults to Haiku 4.5 independently of `REASONING_MODEL`
- [x] Transcript fetching is faked at its boundary in unit tests; at least one `@pytest.mark.integration` test hits real YouTube and asserts a real transcript's shape
- [x] `fpl-ingest-creators` reports videos ingested and transcript stances extracted alongside its existing X-post counts

## Comments

### Built 2026-08-27

**The unverified premise resolved first, as the ticket required.** All 12 handles were probed live. Result: 8 resolve to a channel, 4 do not (`FPL Architect`, `FPL Review`, `Elevenify`, `LiveFPL` — the last three are data/analytics products, as ticket 14 predicted). Of the 8, only **5 are actually productive**:

| Creator | Channel | Cadence |
|---|---|---|
| FPL Focal | `UC72QokPHXQ9r98ROfNZmaDw` | daily |
| Let's Talk FPL | `UCxeOc7eFxq37yW_Nc-69deA` | daily |
| Fantasy Football Hub | `UCcqEr3DfrRwtoF2a1yW8qgQ` | multiple per day |
| FPL Jian Batra | `UCgV5oepPcX1pgSFbEeFU13A` | 2–3 per week |
| Solio Analytics | `UC0LJwjL2pK4VVPnhN28FeMw` | weekly |

The other 3 resolved but are dead or wrong-shaped: **FPL General** has a real channel but has not posted since 2026-07-23 (nothing at all since the season started); **FPL Heisenberg** has one video, a football-freestyle short from 2025-12; **Fantasy Football Scout** resolves to a channel whose only upload is from 2010. All three stay linked — they cost one feed request and zero transcript fetches, and if they start posting we get it automatically.

So the earlier claim that "nine of the twelve publish daily or near-daily video" was wrong, as flagged. It is five.

**Seven creators added to top the roster up** (user instruction: "if there are not enough youtuber creators, find some of the top creators and use those"). Every one verified live with ≥7 of its last 15 uploads since 2026-08-01:

FPL Mate, FPL Raptor, FPL Harry, FPL Family, Planet FPL, FPL BlackBox, FPLtips.

Added with **no X handle** rather than a guessed one, and at `trust_tier = 'medium'` rather than `'high'`. Both are deliberate: fabricating a handle would put a wrong identity in the trust record, and every pre-existing creator being `'high'` is exactly what makes the tier a no-op (ticket 18) — no reason to extend that. This is the first non-degenerate trust data in the table.

Rejected after probing, so nobody re-adds them: **FFGeek** (dormant since 2025-10), **Always Cheating** (ended — last upload is titled "Our Final Podcast"), **FPL Surgery** (dormant since 2021), and **@The59thMinute**, which is a *name collision* — it resolves to an unrelated channel, not the FPL podcast. `@FantasyFootballFix`, `@FPLWire`, `@FPLMilad`, `@FantasyFootballPundits` do not exist as handles.

Net: **15 YouTube sources, 12 of them productive.**

### The constraint the ticket did not know about

**YouTube rate-limits transcript fetching by IP, hard.** 24 concurrent fetches blocked this machine outright, and the block had not lifted an hour later. `project_youtube_transcript_access` recorded that transcripts "work from this machine" — true, but that was one fetch. Volume is the trigger.

This shaped the design more than anything else in the ticket:

- `TranscriptBlocked` is a *distinct* exception from "no captions". No captions → count it, skip the video. Blocked → stop the transcript leg for the entire run, because every further request extends the ban. Whatever was committed stands; idempotency on `(creator_id, source_url)` means the next run resumes.
- Fetches are serialised with a delay, and each creator has a per-run video cap (default 3).
- Existence is checked **before** the fetch, not after via `ON CONFLICT`. The fetch is the scarce resource; spending one on a video already stored is the expensive mistake. There is a test asserting the second run spends zero fetches.
- `YOUTUBE_PROXY_URL` is supported (the library ships proxy config precisely for this ban).

Two properties that look like oversights but are not: the Atom feed returns only a channel's **latest 15 videos with no paging**, so ingestion must run at least weekly or videos are lost permanently; and the per-creator cap means a long gap loses the *oldest* videos in that window, not the freshest.

### Decisions worth not re-litigating

- **Transcripts are stored with `[MM:SS]` markers inline in `content_text`.** Embedding is driven off "rows lacking chunks" rather than off what this run touched ([[project_ingestion_backfill_pattern]]), and that backfill only ever reads `content_text`. Stripping markers at write time would make every already-stored transcript's timestamps permanently unrecoverable. The markers are removed from each chunk's text as it splits.
- **`external_id` is the `UC…` channel id, not the handle.** Handles get renamed; ids do not. And the YouTube handle is *not* the X handle — `@LetsTalk_FPL` on X is `@LetsTalkFPL` on YouTube.
- **The open design question in the ticket is settled as proposed**: nullable `player_name` plus a `subject` column, vocabulary widened to `avoid`/`differential`/`bench`/`chip_timing`/`fixture_view`. A stance with *neither* a player nor a subject is dropped rather than stored — an unattributable opinion would still get counted in ticket 19's consensus.
- **`add_creator` no longer uses `ON CONFLICT`.** No single conflict target works once `x_handle` is nullable, and giving a handle-less creator an X handle later is a plain `UniqueViolation` under the old statement. This was a real bug caught by a test, not a hypothetical. Two creators already holding the name and the handle now raises rather than silently merging two identities.

ADR-0013 records all of the above.

### Two traps found while building

- **A channel page's first `"channelId"` can belong to a *recommended* channel.** Matching it resolved `@LetsTalkFPL` to a completely different channel — with a plausible-looking feed, so it would not have been obvious. Only the `<link rel="alternate">` feed URL is reliably the channel's own. The unit test puts a decoy id ahead of the real one.
- **The RSS feed's entry order is undocumented.** It is usually newest-first, but ticket 18's recency ranking depends on `published_at` being right, so the client sorts explicitly. The canned feed in the test is deliberately out of order.

### What is not verified

The `@pytest.mark.integration` transcript test is **written but has never passed**, because the IP block from the probing above was still in force at the end of the session. Run `pytest -m integration tests/test_transcripts.py` once it lifts (hours, typically). Everything else is verified:

- Live channel resolution + RSS listing against real YouTube — **passes** (`test_youtube_client.py -m integration`).
- The whole transcript path against fakes, including the block, the cap, the no-captions case, and idempotency — **passes**.
- Real Claude extraction of the widened vocabulary is an integration test that has not been run (it costs a live call); the fake-backed equivalents pass.
- **No live `fpl-ingest-creators` run has happened.** The first real run will hit Grok + Claude + Voyage for 19 creators; do it once the block lifts, and expect the transcript leg to report a block if the roster is fetched all at once.

### Pre-existing breakage fixed in passing (not ticket 16)

The suite had **13 failures before any of this work**, all from the `decision`-field addition made earlier the same day ([[project_recommendation_pick_semantics]]): `reasoning/fake.py` was updated with `decision="captain"`, but four test files' own local `Recommendation`/`PlanStep` constructions and two response-shape assertions were not. Fixed mechanically (5 `Recommendation`, 4 `PlanStep`, 2 shape assertions) because a red baseline makes it impossible to tell whether ticket 16 broke anything. Nothing in `src/` changed for this.

### Follow-ups for the next tickets

- **Ticket 17** already has its groundwork: `creator_stances.start_seconds`, `content_chunks.start_seconds`, and `creator_content.source_url` are all populated, so a citation is `source_url + "?t=" + start_seconds` away. What is missing is threading it through the tool return shapes.
- **Ticket 18** now has non-degenerate `trust_tier` data for the first time (7 medium, 12 high) and a real `published_at` to rank on. Note that `published_at` is NULL for every X-sourced row, so recency ranking must handle that rather than sorting NULLs to one end by accident.
- **Ticket 19's** warning about the 591 mis-tagged stances still stands and is untouched by this work.
- `_MAX_VIDEOS_PER_CREATOR = 3` with 12 productive channels is 36 transcript fetches per full run. That is well above what triggered the ban when done concurrently, and untested serially. Watch the first live run and lower the cap or add the proxy if it blocks.

### Follow-up 2026-08-27: the block diagnosed properly, and an age cutoff added

Asked directly whether transcripts are going to work at all, so the block was characterised rather than waited out:

- **RSS feed: 200, fine throughout.** The block is not IP-wide across youtube.com.
- **Watch page: 200, and still serves `captionTracks`.** The page loads; captions exist.
- **`yt-dlp` reproduces it independently: `HTTP Error 429: Too Many Requests` on the caption download.** It even enumerated the tracks (`en-orig, en`) before failing.

So it is a plain per-IP rate limit on the caption endpoint, not a library quirk, not proof-of-origin hardening, and not a design flaw. Rate limits reset. A previous session pulled a full 6,378-word transcript from this same machine, so the capability is proven; the burst of 24 concurrent probe requests is what caused this.

**`max_age_days` (default 14) added as a result.** The walk-back described above was unbounded: a channel would eventually cost 15 fetches across five runs, including pre-season videos and, for a dormant channel, a video from 2010. The cutoff is a relevance filter first — a GW1 captaincy take is worthless by GW5 — and a budget cap second. It also means the three dead/dormant channels (Fantasy Football Scout 2010, FPL Heisenberg 2025-12, FPL General 2026-07-23) now cost **zero** transcript fetches instead of one each. Pass `max_age_days=0` to disable it for a deliberate backfill.

**A second real bug this surfaced:** `published_at` was being handed to psycopg as a raw string, so a malformed feed date aborted the entire run on a `TIMESTAMPTZ` insert. Now parsed once up front, degrading to NULL. Found by the test written for the unparseable-date case, not by inspection.

**Budget after the cutoff:** ~12 productive channels × up to 3 = up to 36 serial fetches per full run, spaced 4s apart. That is a very different request profile from the 24 *concurrent* that caused the ban, but it is genuinely untested. If the first live run blocks, the levers in order of cost are: lower `max_videos_per_creator` to 1–2, raise the delay, then set `YOUTUBE_PROXY_URL`.

### Last AC closed 2026-09-03

The integration test was written when this ticket was built; what it never did was **pass**, because every attempt to reach YouTube's caption endpoint from this IP was blocked. It was left at `[~]` on the honest grounds that an untested test proves nothing.

The block has lifted. `uv run python -m pytest tests/test_transcripts.py -m integration` — **1 passed in 5.19s**, against `@LetsTalkFPL`'s live feed, asserting the real shape: >50 snippets, duration >60s, non-negative and monotonic timestamps.

The same run of `fpl-ingest-creators` landed **32 transcripts across 15 channels with no block at all** (2 videos genuinely had no captions, 30 were older than the 14-day window). That is the first clean transcript run since this ticket was built, and it takes the corpus from 27 video rows to 154 content rows / 1,659 stances.

`YOUTUBE_PROXY_URL` stays unset and remains the lever if the block returns. Worth knowing that the block is intermittent rather than permanent: the throttling in `YouTubeTranscriptSource` (a delay between fetches, newest-first within a per-creator cap) appears to be enough on a good day, and a bad day is still survivable because a blocked run raises `TranscriptBlocked`, stops the transcript leg, and keeps everything already ingested.
