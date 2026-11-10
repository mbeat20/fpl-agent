# 30 — Grok responses are stored once per cited URL, not once per post

**What to build:** Fix `_parse_posts` so one Grok response becomes one content row per *post*, not one per *URL mentioned anywhere in it* — and clean up the rows already written that way.

**Blocked by:** —

**Status:** ready-for-human

## Why

Found on 2026-08-30 while measuring the corpus for ticket 29. The database holds **295 content chunks with only 62 distinct texts**. The most repeated chunk is not a creator's opinion — it is Grok's own narration:

> `Here are the 5 most recent distinct posts from @FPLGeneral matching the criteria...`

**One Grok response is being stored as up to 51 separate `creator_content` rows**, each with a different `source_url` and *the identical full blob* as its text. Measured: 272 of 382 content rows begin with that narration, and 1,507 of 2,217 stances were extracted from them.

### The cause, exactly

`grok_client._BULLET_SPLIT` is `re.compile(r"\n(?=- )")` — it splits on `- ` bullets. Grok returns a **numbered** list:

```
1. **Post URL**: https://x.com/LiveFPLnet/status/2090135273770295573
   **Paraphrase**: Shares predicted effective ownership for key players.

2. **Post URL**: https://x.com/LiveFPLnet/status/2056802835581309025
   ...
```

So the regex matches nothing, `_bullets_by_offset` hits its fallback and returns a single "bullet" spanning the whole text, and `_bullet_containing` therefore returns that whole blob for **every** annotation. Each cited URL is then paired with the entire response.

Verified directly against the real shape: `_bullets_by_offset` returns 1 bullet, and it is the complete text.

This is the same failure mode as ADR-0017's connection-string finding and ticket 24's refetch loop: **it fails open and silently.** Nothing errors; the data just quietly becomes wrong, and it looks plausible.

## What it damages

- **Citations point at the wrong post.** A stance extracted from post #3 in the blob is attributed to whichever URL happened to seed that row. ADR-0014 exists because "a citation the user cannot check is worse than none" — a citation that *is* checkable and wrong is worse still.
- **Stance counts are inflated by 19-51x.** The same opinions are extracted from the same text once per duplicate row. Ticket 19's headline `creators_covering` is safe (it dedupes by creator), but `stances`, `by_stance` totals and ticket 28's "mentions" are all wrong.
- **Extraction cost is multiplied.** The same blob was sent to Claude once per duplicate row.
- **Semantic search (ticket 09) is polluted**: 76% of the corpus is near-identical narration, so nearest-neighbour distance is 0.000 at every percentile.
- **Ticket 29 cannot be built on it.** The largest cluster would be Grok's narration, repeated across 19 creators.

Not damaged, worth noting: the 19 `No matching posts found` rows yielded **zero** stances. The extractor correctly found no opinions in them — that part of the system worked.

## Scope

- Parse numbered items as well as `- ` bullets, and treat the item's own text as that post's content.
- **Change the fallback.** When per-URL text cannot be isolated, emit ONE row rather than N identical ones. Attributing the whole response to every URL is worse than admitting the structure was not recognised — a wrong citation is the one output ADR-0014 rules out.
- Do not store a response that reports no results. `No matching posts found` is not content.
- Add a test with Grok's **real** numbered format, captured from the live data, alongside the existing bulleted one. The current tests presumably use the bulleted shape, which is why this passed.
- **Clean up the existing rows.** Deduplicate `creator_content` by (creator, content_text), keeping one row, and cascade the removal through stances and chunks. This is a destructive operation on live data and needs the user's explicit go-ahead.
- Re-run creator ingestion afterwards to rebuild the corpus properly.

## Notes

- The 92 content rows from the crashed 2026-08-30 run are still unembedded (chunks stuck at 295), so a re-ingest is needed regardless of this fix.
- `UNIQUE (creator_id, source_url)` did not prevent this and could not: the URLs genuinely differ. The invariant that was missing is on the *text*.
- Consider whether `search_creator_posts` should ask Grok for structured output rather than parsing prose at all — the prose format changing under us is exactly what happened here.

## Acceptance criteria

- [x] A Grok response in the numbered format produces one content row per post, each carrying only that post's paraphrase
- [x] A response whose structure cannot be parsed produces one row, never one per cited URL
- [x] A "no matching posts" response produces no content rows at all
- [x] Tests cover the real numbered format captured from live data, and the bulleted format still works
- [x] Existing duplicate rows are removed, with the count reported in the comments
- [x] After a re-ingest, distinct chunk texts are close to the number of content rows rather than a fraction of it
- [x] Stance counts per player are re-measured after cleanup, and the before/after difference is recorded

---

**2026-08-30 — fixed, and the existing rows cleaned up.**

### The parser

`_BULLET_SPLIT` (`\n(?=- )`) is now `_ITEM_SPLIT` (`\n(?=\s*(?:[-*]\s|\d+[.)]\s))`), so numbered items parse as well as bullets. Two behavioural changes matter more than the regex:

- **`_items_by_offset` returns `None` when no structure is found**, instead of one item spanning the whole text. The caller has to be able to tell "this is one post" from "I could not find the posts", because those want opposite handling — conflating them is the entire bug.
- **The unstructured fallback emits ONE row**, not one per cited URL. When we cannot say which post a claim came from, a single coarse row is right; N rows each claiming to be a different post is the wrong-citation failure ADR-0014 exists to prevent. A citation that is checkable and wrong is worse than one that is vague.

`_clean` now strips the enumerator, a `**Post URL**:` header and a `**Paraphrase**:` label, on top of the existing bullet handling. A `_NO_RESULTS` guard drops "no matching posts" responses before they become content — they carry citations to the accounts searched, so "has annotations" was never enough to tell a result from a non-result.

Verified against the real numbered shape captured from the live rows: **3 items → 3 distinct paraphrases**, where the old parser produced 3 copies of the whole blob. Four new tests, and the original bulleted fixture still passes.

### The cleanup

| | before | after |
|---|---|---|
| content rows | 382 | **113** |
| distinct texts | 62 | **113** |
| stances | 2,217 | 827 |
| chunks | 295 | 61 |

**268 duplicate content rows and 1 "no results" row deleted** (the other 18 no-result rows were already duplicates of each other and went with the first pass). `ON DELETE CASCADE` from `creator_content` carried the stances and chunks with them, so this was one delete rather than three kept in step.

Content rows and distinct texts are now **1:1**, which is the acceptance criterion — before, 382 rows held 62 texts. **1,390 stances (63%) were duplicates** of opinions already counted.

Ticket 19's `creators_covering` was never affected, because it dedupes by creator. What was wrong, and is now right: `stances` totals, `by_stance` counts, ticket 28's "mentions", and — the serious one — every citation on a stance from a multi-post blob.

### Note for next time

This is the third failure of the same shape in one session: Supabase's pooler silently dropping `options` (ADR-0017), ticket 24's refetch loop, and this. All three **failed open** — no error, plausible-looking output, wrong data. The common defence is the one applied here: when a parse or a lookup does not recognise what it was given, say so and degrade coarsely, rather than falling back to something that looks like an answer.

Worth considering the ticket's own suggestion as a follow-up: ask Grok for structured output rather than parsing prose at all. The prose format changing underneath us is exactly what happened.

### After the re-ingest

| | before fix | after cleanup | after re-ingest |
|---|---|---|---|
| content rows | 382 | 113 | **128** |
| distinct content texts | 62 | 113 | **128** |
| chunks | 295 | 61 | **831** |
| distinct chunk texts | 62 | — | **831** |
| distinct chunk vectors | 65 | — | **831** |
| stances | 2,217 | 827 | 850 |

Chunks and distinct chunk texts are now **1:1** — 831 of each, against 295 chunks holding 62 texts before. Every embedding is distinct. That is the acceptance criterion, and it is what makes ticket 29 possible: nearest-neighbour distance moved from **0.000 at every percentile** to a real distribution (p05 0.205, median 0.289).

The re-ingest itself added 15 new posts and re-embedded the backlog the crashed run had left (chunks were stuck at 295 with 92 content rows unembedded). YouTube blocked the IP again mid-run, so the transcript leg stopped early — unrelated to this ticket, and the run resumes from there next time.

**23 legacy blob rows deliberately survive.** They carry **36 resolved outcomes** (ticket 24) that cannot be re-fetched, so deleting them would destroy real accuracy history to tidy up prose. Their citations remain coarse — one URL for a bundle — and ticket 18's recency decay already discounts them. The fixed parser will never create more. Ticket 29 excludes their narration chunk from clustering at the chunk level, so their real paraphrases still count.
