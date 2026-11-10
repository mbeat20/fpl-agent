# A Citation is looked up from a tool result, never authored by the model

`Recommendation.evidence` was `list[str]` from ticket 01 through ticket 16. `creator_content.source_url` has existed since ticket 06 and never once reached the user. The effect was that the single most checkable thing the app produces — *a person you chose to trust said this, here it is* — arrived as unsourced prose indistinguishable from the model's own assertion. `preferences.md` says creator opinion is "input to weigh, never the decision itself", and the user cannot weigh what they cannot see.

Ticket 17 makes an evidence line a small model: required prose plus an optional `Citation`.

## The model picks *which* source, not *what* the source is

This is ADR-0004's rule applied to attribution instead of picks, and for the same reason. Left to write its own citation the model would produce a plausible URL, a plausible creator name, and a plausible timestamp — all sampled prose, none of it checkable, and a wrong citation is worse than none because it looks verified.

So the mechanism mirrors `known_players` exactly:

- Creator-opinion tool results carry a `citation_id`, which is `creator_stances.id`.
- `ClaudeReasoning` records a `Citation` per id it has actually returned to the model, built from that row's `creators.name`, `trust_tier`, `creator_content.source_url` and `start_seconds`.
- The submit tools accept only `{text, citation_id}` — there is no url field to fill in. The `citation_id` enum lists only recorded ids, and the property is **omitted entirely** while nothing is citable, because an empty enum is both meaningless to the API and an invitation to invent a value.
- An id the model invents anyway is dropped and the prose kept. Failing the whole request over a bad citation would trade a small loss for a large one.

Three consequences worth naming:

- **`recommend_transfers` results are citable for free.** Ticket 11 already embeds `get_creator_opinions`' stances in each side of a `TransferComparison`, so a transfer question gets sourced creator opinion without a second tool call.
- **An uncited evidence line is a first-class outcome, not a degraded one.** "Haaland leads the squad on xG" has no creator behind it. The field is optional so that line renders honestly rather than reaching for a source.
- **A Stance with no `source_url` never becomes citable.** A hand-built view — a fixture, a test double — has no content row behind it, and yields no citation rather than a citation to nowhere.

## The deep link is assembled in one place, at write time

`deep_link()` in `models.py` is the only code that builds a citation url, and the assembled link is stored on the `Citation` rather than recomputed by each consumer. Two details are deliberate:

- The separator is chosen, not assumed. A stored YouTube url is `watch?v=<id>` and already carries a query string; an X post url does not. A hardcoded `?` produces a url YouTube silently ignores.
- `start_seconds` of 0 is treated as *no timestamp*. `?t=0s` says nothing the bare url does not, and a 0 is far more likely to be a `[00:00]` parse artifact than a claim genuinely made in a video's first second.

For semantic search the timestamp is `COALESCE(stance.start_seconds, chunk.start_seconds)`. The Stance's own marker is where extraction pinned the claim, so it wins; the matched chunk's is the honest fallback for a Stance extracted untimed, since the chunk is what similarity actually found — far better than dropping the user at minute zero of a 40-minute video.

## Changing the evidence shape invalidates cached suggestions, and that is the intended behaviour

`team_suggestions.recommendation` is JSONB, and `get_cached_suggestion` already returns `None` on a `ValidationError` for exactly this class of change. A row written under `evidence: list[str]` now misses and is regenerated.

A `str`-coercing validator on `Evidence` was considered and rejected. It would have saved a dozen test call sites, but it would also have turned that miss into a *hit* — leaving stale, permanently uncitable evidence in front of the user with no signal that it predates the feature. The model boundary is strict about the container for a related reason: iterating a bare string would silently yield one evidence line per character, so a non-list `evidence` is bounced back to the model as a retryable error. A bare string *item* is merely uncited, and is accepted as such.

## No published date for X-sourced citations

Ticket 16 records `published_at` only for YouTube. An X citation therefore carries no date rather than showing `fetched_at` — which is when we first saw the post, not when it was written — in a field labelled as a publish date. The gap is real and matters for recency; ticket 18's recency decay is where it gets addressed, not here with a plausible-looking substitute.
