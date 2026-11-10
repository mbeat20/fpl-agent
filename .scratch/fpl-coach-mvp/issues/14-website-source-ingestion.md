# 14 — Website source ingestion (Fantasy Football Hub, LiveFPL, etc.)

**What to build:** A trusted-source pipeline for non-X websites, so a page like `fantasyfootballhub.co.uk` or `livefpl.net` can feed `creator_stances` the same way an X creator does today.

**Blocked by:** none technically, but needs a scope decision below before it's ready-for-agent.

**Status:** wontfix

## Why this exists

Ticket 06 built creator ingestion as X-only: `creator_pipeline.py` fetches via Grok's X-Search tool, and `creators` is keyed on `x_handle` throughout the schema and tooling. The user asked (2026-08-19) about adding `fantasyfootballhub.co.uk` and `livefpl.net/prices` as trusted sources. Two things came out of that conversation:

- If a site also posts to X, its handle already works today with **zero new code** — just `fpl-add-creator` + `fpl-ingest-creators`.
- `livefpl.net/prices` specifically may already be substantially covered by ticket 08's `predict_price_changes`, which derives rise/fall predictions directly from live FPL transfer-count data — no scraping involved. Worth confirming with the user whether they still want LiveFPL as a source in its own right (e.g. as a cross-check against the model's prediction) before building anything here.

Genuine website ingestion — fetching and parsing an arbitrary URL as a trusted source, not just checking whether it posts to X — is a real architectural addition, not a small extension:

- `creators` (and everything downstream: `creator_content`, `creator_stances`) is keyed on `x_handle`. A website source has no handle, so this needs either a new nullable `source_url` column + relaxed uniqueness, or a parallel table.
- The fetch step itself is a different shape: Grok's X-Search tool does search+retrieval in one call; a website needs its own fetch (`httpx`/similar) plus a parse step per site (or a generic article-extraction approach) before the existing Layer 2 LLM-extraction-into-Stances step can run on it.
- Trust-tier assignment (ADR-0001) and the pgvector embedding step (ticket 06 criterion 4) should carry over unchanged — only fetch + parse are new.

## Open questions (blocking ready-for-agent)

- [ ] Which specific site(s) does the user actually want first — just Fantasy Football Hub, or a general "any URL" capability?
- [ ] Does the user still want LiveFPL prices specifically, given ticket 08 already covers price predictions without scraping?
- [ ] Scrape full articles, or just RSS/summary feeds where available (lower maintenance, more brittle to site changes either way)?
- [ ] Schema approach: extend `creators` with a nullable `source_url`, or a separate `website_sources` table?

## Comments

**2026-08-20:** Created following up on a conversation about trusted sources — see conversation context. Not scoped further; needs the user's answers to the open questions above before an agent can implement it.

**2026-08-21 — closed, wontfix:** User asked to "find highly qualified FPL sites and use those" rather than answer the open scope questions. Checked, and both originally-requested sites — Fantasy Football Hub (`@FFH_HQ`) and LiveFPL (`@LiveFPLnet`) — have active X accounts, plus four more highly-regarded sources were added the same way: Fantasy Football Scout (`@FFScout`), FPL Review (`@fplreview`), Elevenify (`@elevenify`), Solio Analytics (`@SolioAnalytics`). All six added via the existing `fpl-add-creator` + `fpl-ingest-creators` path (ticket 06) — zero new code. A full ingestion run confirmed it works end-to-end: 102 posts ingested, 443 stances extracted, 107 chunks embedded across all 12 creators. Fantasy Football Scout was the standout (60 stances from 10 posts); Solio/LiveFPL contributed content but no stances since their posts don't carry explicit buy/sell framing — expected, not a bug.

This removes the need for the genuine website-scraping architecture described above. Re-open only if the user specifically wants a site with no X presence.
