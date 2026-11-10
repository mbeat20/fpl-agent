# 26 — Podcast ingestion and transcription for audio-only shows

**What to build:** Reach the FPL shows that never appear on YouTube, by pulling their podcast RSS feed and transcribing the audio.

**Blocked by:** 16 — Multi-platform creator sources + YouTube transcript ingestion

**Status:** needs-triage

## Why this is triage rather than ready

The scope is clear but two decisions are genuinely open, and both cost money or time in ways that should be chosen deliberately rather than by an implementing agent.

**First, the honest correction to the original ask.** The Spotify Web API exposes **no transcript endpoint** — Spotify's auto-generated podcast transcripts are not public. There is no "get the Spotify transcript" path to build. So this ticket is podcast RSS + transcription, and the Spotify framing should be dropped from the vocabulary rather than carried as an aspiration.

**Second, most of these shows are already covered by ticket 16.** Let's Talk FPL, FPL Focal, FFScout and others post the same episode to YouTube, where the transcript is free and already timestamped. Transcription is only worth paying for where a show is genuinely audio-only. *The Duplicated Effort question should be settled before building:* list which creators are actually unreachable via YouTube, and if the answer is one or two, this ticket may not be worth doing at all.

## What was verified (2026-08-27)

- Podcast RSS feeds are discoverable via the free iTunes Search API (`https://itunes.apple.com/search?term=<name>&entity=podcast`), which returns a `feedUrl`. Confirmed live feeds for **Fantasy Football Scout — FPL Tips**, **The 59th Minute FPL Podcast** (already cited as a source in `preferences.md`), and **Let's Talk FPL**.
- From the feed, each episode's `<enclosure>` gives a direct audio URL — no scraping.
- **`yt-dlp` and `ffmpeg` are not installed on this machine.** Anything requiring audio re-encoding needs a toolchain decision first.

## Open decisions for triage

1. **Which creators are actually audio-only** after ticket 16 lands. Settle this first; it may close the ticket.
2. **Transcription route.** A hosted API (Whisper via OpenAI, Deepgram, AssemblyAI) is per-minute paid but needs no local toolchain. Local `faster-whisper` is free per-episode but needs a model download and probably `ffmpeg`. A 90-minute weekly podcast is ~90 minutes of audio per show per week — cheap on any hosted service, but it is a recurring cost on an app whose other costs are already Claude, Grok and Voyage.
3. **Whether episode-level transcripts are even useful at this length.** A 90-minute podcast covering 40 players produces a very different extraction problem from a 20-minute video with a clear thesis. Ticket 16's one-call-per-transcript approach may not survive at this length, and chunk-level extraction reintroduces the cost problem ticket 16 avoided.
4. **Diarisation.** Multi-host shows disagree with each other constantly. A Stance attributed to "The 59th Minute" when one host argued against it is a worse input than no Stance. Decide whether speaker attribution is required or whether the show is treated as a single voice.

## Acceptance criteria (draft — confirm at triage)

- [ ] A creator can be given a podcast source, discovered by RSS feed URL
- [ ] Recent episodes are listed from the feed with `published_at`, title, and duration
- [ ] Episode audio is transcribed to timestamped text and stored as `creator_content` with the same shape ticket 16 established
- [ ] Timestamps resolve to a citable position in the episode, matching ticket 17's citation behaviour
- [ ] An already-transcribed episode is never re-transcribed, enforced before any paid call is made
- [ ] The per-episode cost is measured and recorded in the ticket comments
- [ ] Disagreement between hosts does not produce a Stance falsely attributed to the show as a whole

## Triage finding, 2026-09-03 — decision 1 is settled, and it closes the ticket

Decision 1 said: *"Which creators are actually audio-only after ticket 16 lands. Settle this first; it may close the ticket."* Ticket 16 has now landed properly — 32 transcripts in one run, 15 channels checked — so the question is answerable from data rather than guessed at.

**Every creator on the roster is reachable without transcription.** Of 19 creators, 15 have a YouTube source attached and are producing transcripts. The four without a channel are:

| creator | what they are |
| --- | --- |
| Elevenify | data/analytics site |
| FPL Architect | data/analytics site |
| FPL Review | projection model / data site |
| LiveFPL | live-rank data site |

None of them is an audio-only show. They publish numbers, not talk, and their output already reaches the app through the X leg (4–8 content rows each). **There is no audio-only podcast on the roster, so there is nothing for transcription to buy.**

This is the same shape as ticket 14, which was closed `wontfix` on 2026-08-21 once it turned out the website sources could be reached as X creators instead. Decisions 2, 3 and 4 (hosted vs local transcription, extraction at 90-minute length, diarisation) never need to be made, and the recurring per-minute cost never gets added to an app already paying for Claude, Grok and Voyage.

**Recommendation: close `wontfix`.**

### The one loose end, which is a different and much smaller ticket

`preferences.md` opens by citing *"FPL General (Martin McGettigan), 59th Minute FPL Podcast, 'New FPL Draft + Q&A!' (2026-27 season opener)"* — so the user does take standing rules from a show that is **not on the creator roster at all**. FPL General is on the roster as an X creator (11 content rows) but is not linked to the 59th Minute channel.

That is a *creator-roster* gap, not a transcription one, and the fix is one command rather than a ticket: if 59th Minute posts to YouTube, `fpl-link-youtube` attaches it and ticket 16 handles the rest. Worth checking before this ticket is closed, because it is the only show the user has demonstrably said they listen to.

### The loose end, checked — 2026-09-03

Probed the YouTube feed directly for a 59th Minute channel: `@59thMinuteFPL`, `@59thMinute` and `@59thminutefpl` all resolve to nothing, and `@The59thMinute` resolves to an unrelated channel (newest video: *"At 82… I Finally Learned the Truth About 'Someday'"*). There is no FPL 59th Minute channel to link.

The show's host is already covered a different way. **FPL General is on the roster and already has a YouTube source attached** (`UCxj4WVoWBuwXPGJsvUFPVig`), contributing 11 content rows via X. That channel's feed is simply dormant — its newest upload is **2026-07-23**, the season-opener price reaction, six weeks past ticket 16's 14-day freshness window, and everything behind it is World Cup Fantasy from June:

```
2026-07-23  FPL 2026/27 is live! - My Reaction to Player Prices & First Draft!
2026-07-08  QUARTER FINALS PICKS - WORLD CUP FANTASY
2026-06-28  MATCHDAY 4 PICKS - WORLD CUP FANTASY
```

So this is not a missing link, it is a creator publishing to audio and X but not to YouTube this season. **That is exactly the gap this ticket was written for** — and it is a sample of one, on a show whose *standing rules* the user already extracted by hand into `preferences.md` rather than needing per-episode Stances from.

The recommendation is unchanged: **close `wontfix`**. If the 59th Minute later becomes the one show worth paying to transcribe, this ticket is the record of what that would take, and decision 1 is already answered for it.
