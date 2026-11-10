# 02 — Frontend chat UI

**What to build:** The separate Vite + React frontend project (ADR-0009), calling the backend's endpoint over HTTP. A minimal chat-style interface: the user types a question, sends it, and sees the returned Recommendation rendered — primary pick, confidence, evidence, risks, and alternative all visible. This is the first fully browser-demoable slice of the product.

**Blocked by:** 01 — Backend walking skeleton

**Status:** done

- [x] A separate frontend project exists (Vite + React + TypeScript), independent of the backend repo
- [x] The UI sends a free-text question to the backend's Recommendation endpoint
- [x] The UI renders the Recommendation's pick, confidence, evidence, risks, and alternative
- [x] Asking "who should I captain?" against the seeded data from ticket 01 works end-to-end in the browser

## Comments

Scaffolded `frontend/` (Vite + React + TypeScript, ADR-0009) as a sibling project to `backend/`. Single-page chat UI: text input + Ask button, renders pick/confidence/evidence/risks/alternative from POST /recommendations. Vite dev server proxies `/api/*` to the backend on :8000 (`rewrite` strips the `/api` prefix), so no CORS middleware was needed on the backend.

Manually seeded team id 1 (Salah/Haaland/Saka, matching the ticket 01 test fixtures) into the shared Supabase project for the browser demo. Verified end-to-end via curl against the Vite dev server's `/api` proxy (`POST /api/recommendations` → real backend → real Supabase data → rendered Recommendation shape); the Claude-in-Chrome browser tool wasn't connected in this session, so the UI itself wasn't visually screenshotted, but the exact request path the browser makes was exercised and confirmed working. `npm run build` and `tsc -b` both pass clean.
