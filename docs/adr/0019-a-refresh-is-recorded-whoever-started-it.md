# A refresh is recorded, whoever started it — and the app can say how old its data is

ADR-0002 chose on-demand ingestion over a scheduler: nothing runs unless the user asks. That decision stands and this does not revisit it. What it left open is a different question, and one that only becomes visible once there is a dashboard: *how does anyone know when it last ran?*

Until now there was no answer. A run left two traces — `gameweek_state.updated_at` and `max(creator_content.fetched_at)` — and both are side effects of a run rather than records of one. A run that fetched nothing new moves neither. A run that died half way leaves both looking exactly like a success. So the dashboard could not say "this data is nine days old", which is the caveat every panel on it needs, and ADR-0015's commitment that a panel admits what it does not know could not be honoured about the panel's own freshness.

## A run is a first-class thing, in a ledger

`ingestion_runs` records one row per attempt: kind, status, who started it, when it started and finished, the pipeline's own summary, and the error if it failed.

**Recorded regardless of who started it.** `ingestion/jobs.py` is now the single definition of "an ingestion run", and both the CLI and the HTTP endpoint go through it. `fpl-ingest` in a terminal and the refresh button in the browser produce the same row in the same table. A freshness stamp that only knew about half the ways a refresh can happen would be worse than none, because it would be confidently wrong — and the CLI is not going away, so instrumenting only the button was never an option.

**`status = 'running'` is a claim on the resource, not a label.** `ingestion_runs_one_running_per_kind` is a partial unique index, so a second concurrent run of the same kind is a constraint violation rather than a race. For the creator pipeline — which bills Grok, Claude and Voyage per run — that is the difference between one invoice and two from a double-clicked button. Enforcing it in the database rather than by checking first is deliberate: two simultaneous requests cannot both win a check.

Which means a crashed run would hold its claim forever and block every future refresh, silently and permanently — worse than the double-run the lock prevents. So a `running` row older than two hours is reaped as **`abandoned`**, and status reads do the reaping, which makes recovery ride on the user opening the page. That needs no scheduler of its own, which ADR-0002 would have had something to say about.

`abandoned` rather than `failed`, because those are different claims: we know the run stopped being watched, not that it stopped working, and a run killed by a uvicorn reload may well have written most of its data.

## The two kinds are separate endpoints because their costs are not comparable

`POST /ingest/fpl` is roughly a hundred requests to two public APIs and no model calls at all. It is free and can be pressed as often as you like.

`POST /ingest/creators` runs Grok's X Search per creator, Claude extraction per new post or transcript, and Voyage embedding per new chunk. It costs real money per run and takes tens of minutes.

One "refresh everything" button would mean the free refresh could only be had by paying for the other one, and the user would learn to avoid the button. So they are separate, `BILLED` is a property of the source rather than a hardcoded fact in the component that draws the control, and the billed one holds a confirmation step that states its cost before the click.

Both return **202**, not 200. The work happens in a background task, because holding a request open for twenty minutes means a refresh whose fate depends on whether the browser tab stayed open.

## Freshness reports where its number came from

`DataFreshness.source` has three values and they must not be collapsed:

- **`run`** — a recorded successful run. The figure is exact.
- **`proxy`** — no run on record, so the data's own newest timestamp is used instead. Every run predating this table lands here. The figure is a *lower bound*: it says when data last changed, which a run that legitimately found nothing new would not move — and the note says so, in prose, rather than leaving the distinction in a field nobody reads.
- **`none`** — nothing has ever been ingested. **Not the same as old.** Rendering it as an age would invent a refresh that never happened.

That third state is the whole reason this is an ADR rather than a commit. It is the same discipline as `ChecklistItem`'s three states (ADR-0015) and `DeadlineCountdown.source`, and the same lesson STATUS.md §7 draws from three separate bugs: when a lookup does not recognise what it was given, say so and degrade coarsely, rather than falling back to something that looks like an answer.

## What this does not do

It does not schedule anything. There is still no background job, no cron, and no process that runs while nobody is looking. The user presses something, or nothing happens. Automatic scheduling remains the v2 question ADR-0002 named, and it now has a prerequisite in place: a run history to notice silent failures in.
