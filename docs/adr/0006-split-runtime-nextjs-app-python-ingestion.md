---
status: superseded by ADR-0008
---

# Next.js app, separate Python ingestion scripts — two runtimes, not one

The app (API + reasoning + tools) is built in Next.js/TypeScript. Ingestion (Understat scraping, FPL API, Grok/X calls, per ADR-0002's on-demand trigger) is a separate Python process instead of TypeScript, writing into the same Supabase Postgres the Next.js app reads from. Python's scraping and data-pipeline ecosystem is meaningfully more mature for this specific job (Understat/FBref-style scraping, transcript/text processing), and ingestion has no need to share process or deploy target with the app — it runs on-demand, writes to shared storage, and exits. The cost is two runtimes and two dependency sets to maintain instead of one; the boundary between them is the Postgres schema, not a direct API call.

**Superseded**: the backend itself moved from Next.js to FastAPI (ADR-0008), which collapses this split — see ADR-0008.
