# Spec — Richer sources and real planning depth

The second phase of the AI FPL Coach, after the MVP (`.scratch/fpl-coach-mvp/`, tickets 01–15, all closed). Two goals, one enabling the other:

1. **Make the creator layer actually informative.** Today a Creator Stance is extracted from Grok's ~115-word *paraphrase* of an X post. The same creators publish 6,000-word video transcripts daily, verbatim and timestamped. That is the single largest available increase in signal quality, and it changes what the reasoning layer can be asked.
2. **Make long-term planning credible rather than plausible.** The MVP can already emit a multi-week `TransferPlan` (ticket 12), but it plans against data that cannot support it: fixtures only one gameweek deep, no bank, no chip state, no minutes, no enforcement of squad legality, and no memory of the plan once the response is rendered.

## Numbering

Tickets here continue from **16**, not from 01. The codebase annotates itself with bare global issue numbers (`-- Issue 06:` in `schema.sql`, `# Issue 12:` in `api/routes.py`, `# Issue 15:` in `suggestions.py`); restarting at 01 in a second directory would make every one of those comments ambiguous.

## Phases

The user set this order explicitly (2026-08-27):

| Phase | Theme | Tickets |
| ----- | ----- | ------- |
| 1 | Creator intelligence from transcripts | 16, 17, 18, 19 |
| 2 | Complete the state the AI reasons over | 20, 21 |
| 3 | Enforce legality instead of describing it | 22, 23 |
| 4 | A roadmap that persists and is held to account | 25, 27 |
| 5 | Audio-only shows | 26 |

Ticket **24 (durable outcome recording)** sits outside the phases and is the one item with a clock on it: the season is live (GW2 at time of writing), `record_pending_outcomes` only ever resolves the immediately-prior gameweek, and 8 logged recommendations currently have 0 resolved outcomes. Every un-ingested week is history that cannot be recovered.

> **Progress and handoff live in `STATUS.md` in this directory.** Read that before picking up a ticket — it records what is built, what is verified, and the live-database landmine that has now cost real data three times.

## State of the system this phase starts from

Measured against the live database, 2026-08-27:

- 12 creators, **all** `trust_tier = 'high'` — ADR-0001's tier mechanism is a no-op in practice, as is `get_creator_opinions`'s sort by it.
- 290 `creator_content` rows averaging **115 words** (max 229), each of which is Grok's paraphrase rather than the creator's own words (see the note on `creator_content.content_text` in `schema.sql`).
- 295 `content_chunks` — i.e. essentially one chunk per content row, so `_chunk_text`'s 2000-char split has never actually split anything.
- 1,342 `creator_stances` off those 290 rows, none of which expire: `get_creator_opinions` filters on `player_name ILIKE %s` with no gameweek filter and no `LIMIT`.
- `gameweek_state` at GW2; 8 `recommendation_log` rows, 0 with an outcome recorded.

## Verified technical findings (do not re-derive)

Probed live on 2026-08-27 while scoping this spec:

- A creator's YouTube channel id is discoverable from their `@handle` page: fetching `https://www.youtube.com/@LetsTalkFPL` yields `channel_id=UCxeOc7eFxq37yW_Nc-69deA` in the HTML.
- `https://www.youtube.com/feeds/videos.xml?channel_id=<UC...>` returns HTTP 200 with the latest 15 videos (`yt:videoId`, `title`, `published`) — **no API key, no quota**. The `?user=<name>` form is dead (404).
- Auto-generated English captions exist and are listed in the watch page HTML under `"captionTracks"` (`kind: "asr"`).
- **Fetching that `timedtext` `baseUrl` directly returns HTTP 200 with an empty body** — YouTube's proof-of-origin hardening. Do not hand-roll caption fetching.
- `youtube-transcript-api` works from this machine: **936 timestamped snippets, 6,378 words** from a real Let's Talk FPL GW2 video. That is ~55× the current average `content_text`, verbatim rather than paraphrased.
- **Spotify's Web API exposes no transcript endpoint.** Their auto-transcripts are not public. Podcast RSS feeds are discoverable instead via the free iTunes Search API — confirmed live feeds for Fantasy Football Scout, *The 59th Minute FPL Podcast* (already cited in `preferences.md`), and Let's Talk FPL.
- `yt-dlp` and `ffmpeg` are **not** installed on this machine.

## Out of scope

- Multi-user / multi-team. v1's single-team assumption (`MY_TEAM_ID = 1`) holds throughout.
- A scheduler. ADR-0002 (on-demand ingestion) still stands; anything periodic is a CLI the user runs.
- Replacing the X/Grok creator path. Transcripts are an additional source alongside it, not a migration off it.
