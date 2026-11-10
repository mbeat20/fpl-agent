# 17 — Timestamped, cited evidence

**What to build:** Every evidence line in a Recommendation that came from a creator carries who said it and a link to where they said it — for a video, a deep link to the second it was said. The user should be able to click "Let's Talk FPL, 12:23" and land on that moment.

**Blocked by:** 16 — Multi-platform creator sources + YouTube transcript ingestion

**Status:** ready-for-human

## Why

`Recommendation.evidence` is a `list[str]`. `creator_content.source_url` has existed since ticket 06 and has never once reached the user. The result is that the most checkable part of the app's output — "a person you trust said this, here it is" — reads as the model's unsourced assertion. `preferences.md` explicitly says creator opinion is "input to weigh, never the decision itself", which the user cannot do if they can't see the source.

Ticket 16's `content_chunks.start_seconds` makes the citation precise rather than just "somewhere in this 25-minute video": `https://youtu.be/<id>?t=743`.

## Shape

- `evidence: list[str]` becomes a list of a small model carrying the prose plus optional attribution (creator name, trust tier, source url, timestamp seconds, published date). Keep the prose field required so a purely statistical evidence line still fits without inventing a fake source.
- `get_creator_opinions` and `search_creator_opinions` must return the source url and `start_seconds` in their tool results, otherwise the reasoning layer has nothing to attribute *with*. This is the actual work — the model cannot cite what the tool never gave it.
- Attribution must be built from the tool result, not from whatever the model wrote — the same ADR-0004 discipline `submit_recommendation`'s player-id enum already applies to picks. A model-authored URL is a hallucinated URL.
- `TransferPlan`'s `PlanStep.evidence` gets the same treatment.

## Migration note

`team_suggestions.recommendation` is stored as JSONB and `get_cached_suggestion` already returns `None` on a `ValidationError` for exactly this reason (a row predating the `decision` field). Changing the evidence shape will invalidate cached rows; that is a cache miss and a regeneration, not a failure — confirm that path still holds rather than adding migration code.

## Acceptance criteria

- [x] `get_creator_opinions` and `search_creator_opinions` results include each Stance's source url, published date, and `start_seconds` where one exists
- [x] An evidence line derived from a creator carries creator name, trust tier, and a working source link, all built from the tool result rather than from model-authored text
- [x] For a video-sourced Stance the link is a deep link to the second the claim was made, and resolves to that moment when opened
- [x] A purely statistical evidence line (no creator source) is still valid and renders without an empty or fabricated citation
- [x] `TransferPlan` steps cite evidence the same way single-gameweek Recommendations do
- [x] The front end renders citations as clickable links naming the creator, in both `RecommendationCard` and `TransferPlanCard`
- [x] A `team_suggestions` row cached under the previous evidence shape produces a cache miss and clean regeneration, not a 500

## Comments

### What was built

**Backend**

- `models.py`: new `Citation` and `Evidence` models, plus `deep_link(url, start_seconds)` — the only code in the project that assembles a citation url. `Recommendation.evidence` and `PlanStep.evidence` are `list[Evidence]`. `CreatorStanceView` and `SemanticCreatorMatch` each gained `citation_id`, `source_url`, `start_seconds`, `published_at`.
- `tools/get_creator_opinions.py`: joins `creator_content` and returns `creator_stances.id` as `citation_id`.
- `tools/search_creator_opinions.py`: same join, and `COALESCE(s.start_seconds, nc.start_seconds)` so a Stance extracted without a `[MM:SS]` marker still lands near the claim via the chunk that similarity actually matched.
- `reasoning/claude.py`: `known_citations` mirrors `known_players` exactly. `_record_known_citations` collects a `Citation` per stance the model has been shown; `_evidence_schema` builds the `{text, citation_id}` item with the enum of those ids; `_build_evidence` resolves an id back to the recorded `Citation`.
- `fpl_rules.py`: two bullets added to the Honesty section telling the model to attach `citation_id` and to leave a statistical line uncited.
- `CONTEXT.md`: **Citation** added to Language. `docs/adr/0014-citations-are-looked-up-not-authored.md` written.

**Frontend**

- `types.ts`: `Citation`/`Evidence` interfaces, `evidence: Evidence[]`, and `timestampLabel()` (743 → `"12:23"`, hours only when there are any).
- New `components/EvidenceLine.tsx`, shared by `RecommendationCard` and `TransferPlanCard` so a plan step's evidence cannot drift into a second, weaker rendering. It never assembles a url — the backend hands it one.
- `.citation` / `.citation-stamp` in `index.css`: a quiet monospace pill that brightens to `--accent` on hover.

### Decisions worth not re-litigating

**No `str`-coercing validator on `Evidence`.** It would have saved a dozen test call sites, but it would also have turned the required cache *miss* into a cache *hit* — leaving stale, permanently uncitable evidence in front of the user with no signal that it predated the feature. AC7 asks for a miss; a validator would have quietly defeated it.

**Strict about the container, forgiving about an item.** A non-list `evidence` is bounced back to the model as a retryable tool error, because iterating a bare string would silently produce one evidence line per character. A bare string *item* becomes an uncited line — a model slip should cost the citation, not the whole answer. Both paths are tested.

**The `citation_id` property is omitted from the schema entirely while nothing is citable**, rather than sent with an empty enum. An empty enum is meaningless to the API and reads as an invitation to invent a value.

**`start_seconds` of 0 is treated as no timestamp.** `?t=0s` says nothing the bare url does not, and a 0 is far more likely to be a `[00:00]` parse artifact than a claim genuinely made in a video's first second.

**X-sourced citations carry no published date.** Ticket 16 only records `published_at` for YouTube. Showing `fetched_at` — when we first saw the post — in a field labelled as a publish date would be a quiet lie. The gap is real and matters for recency; it belongs to ticket 18, not to a plausible-looking substitute here.

### Verification

- `tests/test_cited_evidence.py`, 26 tests: `deep_link` url assembly (both separator cases, the zero/negative case), `Citation.from_stance` including the no-url case, both tools against seeded YouTube *and* X content, the COALESCE preference *and* its fallback, and eight `ClaudeReasoning` tests with the Anthropic client mocked.
- The adversarial ones are the point: `test_the_model_cannot_smuggle_in_its_own_url` (extra `url`/`creator_name` keys in the tool call are ignored), `test_an_invented_citation_id_is_dropped_and_the_prose_survives`, `test_a_stance_with_no_source_url_never_becomes_citable`, and `test_no_citation_id_is_offered_before_a_creator_tool_has_returned_anything`.
- `test_get_cached_suggestion_returns_none_for_an_old_shape_row` writes an old-shape row into the real JSONB column and asserts a miss, then round-trips a new-shape one with a citation to prove the miss is the old shape's fault and not a broken read.
- **Live check against the real roster:** `get_creator_opinions(conn, "Salah")` returns 21 stances, every one now carrying a working `x.com` source url. There are 290 X content rows and 1,342 stances already in the database, so citations have real data to work with immediately — this feature did not have to wait for the YouTube leg.
- Frontend: `tsc -b` and `oxlint` both clean.

### Why AC3 is `[~]` and not `[x]`

The url *form* is verified, and verified against YouTube itself: `GET https://youtu.be/<id>?t=743` returns a 303 to `https://www.youtube.com/watch?t=743&v=<id>&feature=youtu.be`, which is the exact parameter set `deep_link` produces (order-independent; YouTube accepts both `743` and `743s`, and `743s` is what its own "copy link at current time" emits). Fetching a real assembled link returns 200.

What is **not** verified is the click itself. The seek is handled client-side — the served HTML carries `startTimeSeconds: 0` regardless — so an HTTP fetch cannot prove the player lands at 12:23. And there is still **no real timestamped YouTube Stance in the database** to click: re-checked after the suite finished, `creator_content` has **0** rows with `platform = 'youtube'` and `creator_stances` has **0** rows with a `start_seconds`. (An earlier reading of "1 of each" was a live test fixture mid-run, not real data.) That is ticket 16's outstanding gap, not a gap here — no live `fpl-ingest-creators` run has happened, because the YouTube caption endpoint 429'd this IP.

**To close it:** once the block lifts, run `fpl-ingest-creators`, then ask a captaincy question and click a citation. Everything on this side is in place and tested against seeded equivalents of exactly that data.

### Follow-ups this opens

- **Ticket 18 (recency decay / trust tiers)** now has `published_at` on the citation to decay against, and the missing X publish date is its first real problem to solve.
- **Ticket 19 (consensus / bandwagon meter)** can cite each contributing voice rather than asserting a count.
- `creator_content.title` is stored but not surfaced. A citation reading "Let's Talk FPL — GW3 Team Selection, 12:23" would be materially more useful than the creator name alone; deliberately left out to keep this ticket to the five fields it specified.
- `recommendation_log` still stores only the pick. Logging which citations backed a decision would make ticket 24's outcome recording able to score *sources*, not just picks.

### AC3 closed 2026-09-03 — and it was not just waiting for data

The block on the YouTube leg lifted. `fpl-ingest-creators` landed **32 transcripts in one run**, and the numbers the section above was waiting on are now real: **1,446 video-sourced stances, every one of them carrying a `start_seconds`** (1,446 of 1,446 — no video stance lacks one).

Clicking one then surfaced a bug the seeded fixtures could not have caught, because a fixture's timestamp is always a sane number.

**162 of the 1,446 deep links (11%) pointed past the end of their own video.** The worst was `&t=113520s` — 31½ hours — on a video 7,035 seconds long. The error is systematic, not random: the transcript is rendered to the extractor with `[MM:SS]` markers, and the model periodically reads one as `H:MM`. `[31:32]` becomes 31 hours 32 minutes (113,520s) instead of 1,892s. Every out-of-range value in the corpus decomposes exactly that way.

This was the last part of a Citation still taken on the model's word. ADR-0014 says a citation is *looked up, never authored*; `start_seconds` was authored, and 11% of the time it was wrong. `content_chunks.start_seconds`, which is recovered from the transcript's own markers instead, had **0** out of range — which is the confirmation that the marker parser is sound and the fault is arithmetic on the model's side.

**Fixed** by `in_range_start_seconds` in `creator_pipeline.py`, applied at the same boundary that already enforces `STANCE_VOCABULARY` — the point where the model's output stops being trusted. A `start_seconds` past its content's `duration_seconds`, or negative, or not an integer, is stored as NULL.

**Deliberately not repaired.** 113,520 unpacks to 31h32m, and 31×60+32 = 1,892 would land neatly inside the video. Reinterpreting it that way is a guess at *which* mistake the model made, and a citation pointing confidently at the wrong sentence is worse than one that opens the video at 0:00 — which is precisely the graceful path this ticket already built for untimestamped content. STATUS.md §7.

- `tests/test_start_seconds_range.py`, 14 cases, pure.
- `scripts/clear_out_of_range_timestamps.py` cleaned the 162 rows already stored. It also checks `content_chunks`, so if the marker parser ever does break it is visible.
- Remaining 1,284 timestamped stances all verified inside their video's duration.

The one thing still not machine-verifiable is unchanged and stated above: the seek is client-side, so no HTTP fetch can prove the player lands at 12:23. What *can* now be checked — that the second cited exists in the video at all — is checked, and was failing 11% of the time until today.
