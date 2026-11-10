---
status: superseded by ADR-0009
---

# Frontend is a separate Next.js project from the backend API

Rather than one Next.js app serving both UI and API routes, the frontend is its own Next.js project, calling the backend's API routes over HTTP as a client. There's no immediate multi-consumer need in v1 (single user), so this is a deliberate choice to keep the API boundary explicit from day one rather than merging UI and API and splitting them apart later — it also keeps a future React Native app as a clean second consumer of the same backend API, rather than requiring an extraction step. The cost is two Next.js projects to run and deploy instead of one.

**Superseded**: the backend is no longer Next.js (ADR-0008), so "as a separate Next.js project" no longer applies as written. The separate-project call itself stands; see ADR-0009 for the frontend's actual framework.
