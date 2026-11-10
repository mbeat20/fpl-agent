# A theme is named by assembly and quoted for evidence

Ticket 29 labelled each theme with its cluster's medoid — a real sentence a real creator actually said — on the grounds that a generated label is exactly the hallucination surface ADR-0014 closed for citations: plausible, unverifiable, and wrong in a way that looks authoritative.

That reasoning is correct and is not being overturned. But on 2026-09-04 the panel was opened in a browser for the first time, and the consequence became obvious. Its themes were called things like:

> "Whereas with now, right, you have this high floor created for you by the variables discussed beforehand."

> "Bench one, Fodden, Odigard, Brobby, or Cat Lewen."

Both real, both correctly cited, and neither tells the reader what the theme is. **A quotation is evidence, and evidence does not make a heading.** Conflating the two meant the panel had no headings at all.

## Three changes, in the order they were needed

**1. The seed and the label are different choices.** `_cluster` picks a seed because its neighbourhood spans the most distinct creators — that is what makes a cluster a cross-creator theme rather than one person repeating themselves, and it must not change. Readability is a separate question that nothing was asking. The label is now chosen from among the cluster's *members* (all near-neighbours of the seed, so any of them represents it) by a `quotability` score: starts and ends a sentence, sits in a readable length band, low filler density, penalised for being a read-out list of names, rewarded for naming a proper noun. The citation follows the chosen quote rather than the seed — a link pointing at different words than the ones above it is precisely the misattribution ADR-0014 exists to stop.

That removed the mid-clause fragments. It could not fix the underlying mismatch.

**2. A theme carries a `title` as well as a `label`.** The title is **assembled, never written**: a term from a closed FPL vocabulary (`_TOPICS` — chips, blanks, captaincy, price changes, injuries, rotation, fixture runs, differentials) matched literally in the creators' own words, plus the players the cluster's stances resolved to. When it recognises neither, it reports the count — *"Discussed by 5 creators"* — rather than inventing a subject.

This does not breach ADR-0014. The rule that ADR imposes is that nothing on the panel may be prose a model wrote, because an unverifiable sentence is the hallucination surface. A title built by counting matched terms is not prose: there is no model anywhere in it, every element is checkable against the corpus, and it degrades to a bare count rather than guessing (STATUS.md §7).

**3. The title's players are ranked by distinctiveness, not frequency.** This one was found by looking at the output twice.

Ranked by raw mentions, `Bruno Fernandes` headed **six titles out of eight** — because the most-mentioned player in a cluster is the most-mentioned player in every cluster. So the title ranks on mentions divided by how many clusters the player appears in at all.

Ranked that way and fed the unfiltered name list, it swung the other way and headed themes with `Chiao`, `Chelsea defense`, `Califiuri` and `Arne Slot` (a manager) — rare *precisely because* they are extraction noise, and dividing by cluster count actively promotes what appears once.

So the title is fed only stances that **resolved to a real `fpl_player_id`** (an unverified string must not become the panel's assertion about what a theme is), and only players named in **at least two** of the cluster's content rows (said once is a post, not a theme). The panel's own `players` list keeps the frequency ranking and the unresolved names, because there the question is "who did creators talk about" and a mangled name is often a real player.

Two rankings, because they answer two questions: the list says *who this theme discusses*, the title says *what makes this theme not the others*.

## Known limitation

The topic vocabulary is coarse — four of eight live themes resolve to "Captaincy" — and two clusters with identical resolved-player sets still produce identical titles. Widening `_TOPICS` is the obvious lever and it is a closed list on purpose: an open-ended keyword extractor would title themes with whatever noun happened to be frequent, which is a worse version of the problem this replaced.
