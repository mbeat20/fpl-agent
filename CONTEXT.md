# AI FPL Coach

A personal AI assistant that recommends Fantasy Premier League (FPL) decisions — captaincy, transfers, chip timing — backed by evidence from official data, statistics, and trusted creator opinions.

## Language

**Creator Stance**:
A single creator's extracted opinion, pulled from their raw content (post, video transcript, article) by the extraction layer. One ingredient among several that feeds a Recommendation. Usually about a player (buy/sell/hold/captain/avoid/differential/bench), but a Stance can also be about no player at all — when to play a chip, or how good a club's fixture run is — in which case it names its `subject` instead. A Stance is never a pick: it is evidence the AI weighs, and it can never itself become a Recommendation's chosen player (ADR-0004).
_Avoid_: Recommendation (when referring to a creator's opinion), creator opinion, creator recommendation.

**Creator Consensus**:
How many *distinct* trusted creators hold a given view on a player in a given gameweek — and, more importantly, how that count moved since the previous one. Counted per creator and never per Creator Stance: one prolific account posting the same take six times is one creator. Gameweek attribution comes from when the content was published, measured against that gameweek's deadline, never from the gameweek an ingestion run happened to be tagged with. Consensus is evidence *about* evidence: a settled majority and a four-day surge produce the same count and mean opposite things, so the movement is always stated alongside the level, and a heavy or fast-moving count belongs in a Recommendation's risks rather than in its case.
_Avoid_: Creator Stance (a Consensus is a count over many Stances, not one), ownership (that is FPL's own `selected_by_percent`, a fact about managers rather than about creators), popularity, hype.

**Recommendation**:
The AI's own synthesized output to the user (e.g. "Captain: Salah, Confidence: 92%"), built by combining Creator Stances, statistics, fixtures, and reasoning. Always carries confidence, evidence, risks, and alternatives. Each evidence line is prose plus an optional Citation.
_Avoid_: Creator Stance (when referring to the AI's own output), suggestion, advice.

**Citation**:
The attribution attached to one of a Recommendation's evidence lines: which creator said it, their trust tier, a link to where they said it, and — for a video — the second they said it. Always looked up from the Creator Stance the reasoning layer was actually shown, never written by the model, for the same reason a pick is not parsed out of prose (ADR-0004, ADR-0014). An evidence line resting on statistics rather than on a creator carries no Citation, and that is correct rather than incomplete.
_Avoid_: source (too vague — a Citation is specifically the attribution on one evidence line, not the content row it points at), reference, link.

**Horizon**:
How far out a Recommendation looks — this gameweek's captain/transfer call, a multi-week transfer plan, or season-long strategy. A parameter of the question asked, resolved live from fixtures/data each time. Never persisted.
_Avoid_: Mode, timeframe (when used loosely — Horizon is specifically "how far the Recommendation looks ahead").

**Preference**:
A standing trait of the user that holds regardless of Horizon (e.g. "dislikes knee-jerk transfers", "takes captain risks when chasing rank"). Colors every Recommendation. Distinct from a one-off, single-session ask ("give me the safest pick this week"), which is never persisted.
_Avoid_: Memory (too broad — Preference is the specific persisted subset of it), setting.

**Instinct**:
The user's own early-week note on a transfer or captaincy call they're considering (e.g. "thinking Rogers → Enzo"), logged before deadline-day content and pressure build up. Gameweek-scoped: persists only until that gameweek's deadline, then discarded — never carried forward like a Preference. When the user later asks about that same decision, the AI surfaces the original Instinct alongside its current Recommendation so the user can judge whether their thinking genuinely evolved or drifted toward a bandwagon.
_Avoid_: Preference (season-long, never expires), Recommendation (the AI's output, not the user's own note), Horizon (a property of a question, not a stored note).

**Checklist Item**:
One checkable fact about the squad as it currently stands, shown on the deadline screen — captain set, a flagged player in the XI, bench order, bank, a blank fixture. Deterministic and free: derived from the database alone, never from a reasoning call, so opening the dashboard costs nothing. Deliberately not a Recommendation: an item states the fact and stops ("two players in your XI are flagged: X (injured), Y (doubtful)"), and never says what to do about it. Carries three states rather than a boolean, because "unknown" — before ingestion has run, or for anything resting on data FPL does not publish — is a real answer that must not be collapsed into either "fine" or "broken".
_Avoid_: Recommendation (advice; a Checklist Item is an observation), warning/error (an item needing attention means "worth your eyes", not "you are wrong"), alert.

**Roadmap**:
A multi-week plan the user is *following*, as opposed to a TransferPlan, which is one answer to one question. The difference is that a Roadmap still exists next week, so the user can be shown to have drifted from it. Persisted and **versioned, never overwritten** — the history is the value, because "last week's roadmap said something different" is exactly the signal `preferences.md` asks for when distinguishing genuine reassessment from bandwagon influence. Every step is checked against a running squad, bank and free-transfer count rather than in isolation, and a Roadmap that fails that check is never returned as one. Considered *completed* rather than live once every step's gameweek has passed, so a month-old plan stops looking authoritative on its own.
_Avoid_: Horizon (a property of a question, resolved live and never persisted — a Roadmap is a stored artefact), TransferPlan (the response shape a Roadmap stores, not the Roadmap itself), strategy, schedule.
