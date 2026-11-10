# 29 — Common themes, clustered from the embeddings we already have

**What to build:** A panel showing what creators are collectively talking *about* — not player-by-player, but as themes that emerge from the content itself — with each theme labelled by a real quote and linked to the players it names.

**Blocked by:** 28 — Creator attention board (shares the panel's framing, not its code)

**Status:** ready-for-human

## Why

Ticket 28 answers "which players". This answers the question underneath it: *"what is the argument this week?"* — the Arsenal rotation nobody is pricing in, the case for wildcarding now, the double-gameweek nobody has noticed. Those are the things a creator's audience actually takes away, and the database has no representation of them at all.

The obvious place to look is `creator_stances.subject`, the field for a Stance about no player. **It is a dead end.** Measured 2026-08-30: **11 non-player stances out of 1,605**, spread across 9 `subject` values, all of them chip names — and case-fragmented (`Wildcard`/`wildcard`, `Triple Captain`/`triple captain`, `Max Captain`/`Max Cap`). There is no theme corpus to read.

There is, however, a fully populated one to *derive* from: **295 content chunks, all 295 embedded** (`content_chunks.embedding`, pgvector, 1024-dim, ADR-0005). That was built for ticket 09's semantic search and is used for nothing else. Themes are what those embeddings look like when you stop querying them one question at a time.

## The structural insight

Themes and players are already joined:

```
content_chunks → creator_content → creator_stances → player_name / fpl_player_id
```

So clustering the chunks gets the players for free. A theme is not a separate thing to be extracted and then matched up — it is a set of content rows, and those rows already have Stances hanging off them. One pass answers both halves.

## Scope

**Cluster by cross-creator density, not by k-means.** For each chunk, count the *distinct creators* among its nearest neighbours within a cosine threshold (`<=>`). A chunk whose neighbourhood is one creator is that creator repeating themselves; a chunk whose neighbourhood spans seven creators is a theme. Take the dense chunks as seeds, absorb their neighbours, dedupe overlapping clusters. At 295 chunks this is trivially fast and needs **no new dependency** — pgvector plus a small Python pass, not scikit-learn.

**Label each theme with its medoid — a real sentence a real creator actually said.** This is the part that matters. ADR-0014 exists because a model-authored citation is a hallucinated citation, and a model-authored *theme label* is exactly the same hallucination surface: plausible, unverifiable, and wrong in a way that looks authoritative. A medoid chunk is not a label the app wrote — it is a quote, with a `source_url` and a `start_seconds`, so `deep_link()` produces a timestamped link the user can click and check. Every word on the panel should be either a count or a quotation.

```
7 creators · 23 mentions · new this week
"the Arsenal rotation is the thing nobody's pricing in"  — @FPLGeneral, 12:23
Players named: Saka, Ødegaard, Timber
```

**Size a theme by creators, not by chunks.** Ticket 19's rule, and for the same reason: one prolific account posting six times is one creator. Report both numbers — `creators` is how many people hold the view, `mentions` is how loudly it was said, and collapsing them in either direction misleads.

**New versus carried over.** Same principle as ticket 19's movement and ticket 25's check-in: the derivative is the signal. A theme first appearing in the last 48 hours is news; one running for three weeks is background. Ticket 18's decoded `published_at` supports this.

**Free to open.** No Claude call, no live FPL call (ADR-0015). Clustering 295 vectors is cheap; the whole point of using the embeddings already paid for is that this panel costs nothing to render.

**Also fix `subject` normalisation.** Small, real, and in scope because it fragments the one genuinely thematic signal already being extracted: casefold and trim at extraction time so `Wildcard` and `wildcard` are one bucket. Migrate the existing rows.

## Notes

- The corpus is small and X-dominated (290 of 290 content rows were `platform = 'x'` before ticket 16's YouTube path had produced anything). Chunks are therefore roughly one-per-post and short. Tune the threshold against that reality rather than against an assumption of long transcripts — and re-tune if YouTube ingestion starts landing, because a 40-minute transcript chunks very differently.
- A cluster of one is not a theme. Set a floor on distinct creators and drop everything below it rather than showing singletons.
- Do not summarise a cluster with an LLM "just for the label". It reintroduces both a per-render cost and the exact hallucination surface the medoid design avoids. If a label genuinely cannot be read from a medoid, that is a signal the cluster is not coherent — drop it.
- Carry ticket 28's framing. A theme panel is a bandwagon amplifier by default, and `preferences.md` treats heavy coverage as a risk.
- Once ticket 24's outcome history accumulates, a theme can be scored on what its stances actually returned. That is the honest counterweight to popularity — note the hook, do not build it here.

## Acceptance criteria

- [x] Themes are derived by clustering `content_chunks.embedding`, with no new library dependency and no LLM call
- [x] A theme is only shown when it spans a minimum number of *distinct* creators; singletons and one-creator repetition are dropped
- [x] Each theme is labelled by a real chunk of creator text, never by generated prose, and links to its source with a timestamp where one exists
- [x] Each theme lists the players its underlying Stances name, resolved through `creator_content` rather than by matching text
- [x] Both a creator count and a mention count are shown, and they are not conflated
- [x] Themes first appearing in the last 48 hours are distinguishable from ones carried over
- [x] `subject` is normalised at extraction time and existing rows are migrated, so chip-timing stances stop fragmenting across case variants
- [x] The panel renders nothing when there is too little content to cluster, rather than showing noise as though it were signal
- [x] Clustering the full corpus is fast enough to serve on a page load without caching

---

**2026-08-30 — built, on a corpus that had to be cleaned first.**

`GET /creators/themes` → `ThemeBoard`, rendered by `ThemesPanel`. `themes.py` clusters `content_chunks.embedding` by cross-creator density; no new dependency, no LLM anywhere — pgvector computes the distances and the clustering is a short greedy pass.

### The corpus had to be fixed before this could mean anything

The first thing this ticket did was measure the embedding distances to pick a threshold. Nearest-neighbour distance came back **0.000 at every percentile** — 295 chunks, 62 distinct texts. That was **ticket 30**: one Grok response stored as up to 51 identical rows. Building clustering on it would have produced a top theme of Grok narrating itself.

After the fix and cleanup the distribution is real: nearest-neighbour **p05 0.205 / p25 0.258 / median 0.289**, with pair counts of 44 / 219 / 1042 / 5220 at thresholds 0.20 / 0.25 / 0.30 / 0.35. **0.26** sits where the count is still selective. Measured, not guessed, and reported on the board so the one knob that changes what a theme *is* travels with the answer.

### The label is a quotation, and that is the design

Every word on the panel is a count or something a creator actually said. A generated theme name would be exactly the hallucination surface ADR-0014 closed for citations: plausible, unverifiable, authoritative-looking, wrong. The medoid is a real chunk with a real `source_url` and `start_seconds`, so `deep_link()` makes the label clickable and checkable — and a test asserts the label is literally one of the stored `chunk_text` values.

One refinement the live data forced: a transcript is chunked on length, not grammar, so a medoid routinely starts mid-clause (*"in any specific week they're more explosive than Sangaré is..."*). `_label` advances to the first sentence boundary when the chunk starts lowercase and one is near. Still a real quotation, now readable as one.

### Two defects the live run exposed, both fixed

- **The narration still clustered.** 23 legacy blob rows survive the ticket-30 cleanup because they carry **36 resolved outcomes** (ticket 24) that cannot be re-fetched. Their first chunk is Grok's boilerplate, near-identical across creators, so it clustered into the largest theme on the board — 9 creators, 13 mentions. Excluded at the **chunk** level rather than the row level, so those rows' later chunks, which are real paraphrases, still count.
- **Player lists were useless.** A forty-minute transcript names twenty players, so a theme listed all of them alphabetically. Now ranked by how many of the cluster's content rows mention each, capped at six — the players the theme keeps returning to rather than everyone who appears once.

### A conflation the tests caught

`build_themes` guarded on `len(chunks) < min_creators`, so asking for themes held by 99 creators reported *"too little creator content — run fpl-ingest-creators"*. Those are different facts: a corpus that does not exist and a corpus that does not agree. Guarded on having any content at all now, and "no group of N creators is saying the same thing" is returned as the real answer it is.

### Not done

`subject` normalisation is left for ticket 30's follow-up rather than done here — it turned out to be an extraction concern, and after the cleanup there are too few non-player stances (11 across 9 case-fragmented values) for it to change anything this panel shows.

### `subject` normalisation — built 2026-09-03

The ticket left this open on the grounds that there were "too few non-player stances for it to change what the panel shows". There are now **188**, and the fragmentation was worse than case variants:

| one idea | reached the database as |
| --- | --- |
| wildcard | `wildcard` 25 · `wild card` 7 · `Wildcard` 2 |
| free hit | `free hit` 13 · `free_hit` 3 · `Free Hit` 2 |
| bench boost | `bench boost` 17 · `Bench Boost` 2 · `bench_boost` 1 |
| triple captain | `triple captain` 13 · `Triple Captain` 3 · `triple_captain` 1 |
| Man City | `Manchester City` 8 · `Man City` 3 |

That is not cosmetic. Ticket 19 counts **distinct creators holding a view**, so a subject split three ways splits the count three ways: a chip six creators agreed on presented as three weak opinions instead of one strong one. 35 distinct subjects → **25**, and `wildcard` went from a top count of 25 to its true **34**.

**`subjects.py`**, pure, applied in `_store_stances` and backfilled by `scripts/normalise_subjects.py` (49 rows renamed). Two closed vocabularies and nothing else:

- **Chips** — a fixed set FPL defines. Case, spaces and underscores are squashed away, so there is no way to be wrong about what `wild_card` means. FPL's own API spellings (`3xc`, `bboost`) map to the same entries, so a chip named in `chips_used` and a chip named by a creator are one subject.
- **Clubs** — resolved against the `clubs` table ingestion already populates from bootstrap. An authoritative lookup, the ADR-0004 shape. Exact on name or short name, then a prefix relation in either direction, which covers both real creator habits: dropping the suffix (`Coventry` → `Coventry City`) and adding one (`Newcastle United` → `Newcastle`). Ambiguity abstains — a bare `Man` reaches both Manchester clubs. A short `_ALIASES` table carries the dozen clubs whose common name shares no prefix with FPL's (`Tottenham` → `Spurs`, `Nottingham Forest` → `Nott'm Forest`); each line is a fact about English football rather than a pattern, so it is written out rather than inferred.

**Not repaired: `Brenford`**, which appears 3 times. Merging it into Brentford needs a fuzzy threshold, and one loose enough to catch a dropped `t` is loose enough to merge two real clubs — the same call `player_names` makes about `Kinski` for Kinsky. It stays as it is, visibly wrong rather than invisibly guessed.

Anything that is neither chip nor club (`transfer`, `Commentary`, `price rises`) is returned **verbatim rather than dropped** — an unrecognised subject is still a real subject, and `creator_stances_subject_check` requires a player-less Stance to carry one.

`tests/test_subjects.py`, 34 cases, including an idempotence test: the normaliser's own output must be a fixed point, or the migration script rewrites rows every time it runs.
