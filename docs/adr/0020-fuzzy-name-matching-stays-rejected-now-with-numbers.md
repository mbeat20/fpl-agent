# Fuzzy player-name matching stays rejected — now with numbers

`player_names.py` has always refused edit-distance matching, and said why in its docstring: `Kinski` for `Kinsky` is a mishearing one character from a real player, and so are plenty of genuinely *different* players, so a threshold loose enough to catch the first is loose enough to invent attributions.

That was an argument. On 2026-09-04 it became a measurement, because the question keeps getting re-opened — 33% of player-named Creator Stances resolve to no `fpl_player_id`, YouTube auto-captions visibly mangle names (`D Kyper`, `Cat Lewen`, `Brobby`, `Odigard`), and "just add fuzzy matching" is the obvious next move. This ADR exists so it does not have to be re-derived a third time.

## What was measured

491 unmatched player-named Stances. Each unmatched name's surname was collapsed to a consonant skeleton (vowels dropped, like-sounding consonants mapped to one class) and compared against every pool player's surname:

| share | bucket | what it is |
| ----- | ------ | ---------- |
| 34% | no pool match at all | real players who are not in the Premier League |
| 25% | skeleton too short to code safely | 2-character codes collide with everything |
| 24% | phonetically ambiguous | several pool players; correctly abstained |
| **16%** | **phonetically unique** | **the only bucket a repair pass could act on** |

Separately, transcript-sourced Stances match at 56% against X-sourced Stances at 79%. The gap is real. It is not mostly mishearings.

## Why the 16% is not the opportunity it looks like

Roughly half of those unique candidates are the wrong person:

```
Savinho          → Savona             two players
Vinícius Júnior  → Iling-Junior       one is not in the league
Nico Williams    → Neco Williams      two players
Jhon Durán       → Alcaraz Durán      two players
Che Adams        → Tyler Adams        two players
Yerson Mosquera  → Cristhian Mosquera two players
Arne Slot        → Seelt              a manager, not a player
```

Narrowed further — to cases where the surname matched *exactly and uniquely* and only the forename disagreed — the forename veto turns out to be **right about 2.5 times for every time it is wrong**. Relaxing it would recover ten or so Stances and misattribute twenty-five.

A misattribution is not a symmetric cost against a miss. An unmatched Stance is invisible: ADR-0004 means it can never reach a Recommendation, and that is the correct outcome for a player who is not in the game. A wrongly matched Stance puts an opinion a creator never held into the trust record, lets ticket 19 count it toward a consensus, and lets ticket 24 score an outcome against it. It is wrong in a way that looks authoritative and compounds.

## What was built instead

Two changes, both finite and reviewable, because each is a fact about names rather than a guess about a string:

- **`_FORENAME_VARIANTS`** — a curated table of given names that are the same name: diminutives (`Andy`/`Andrew`, `Tommy`/`Thomas`) and transliteration variants (`Ryan`/`Rayan`, `Nicholas`/`Nicolas`, `Enso`/`Enzo`). Applied to both sides, because FPL's spelling is the arbitrary one as often as the creator's. It deliberately does **not** contain `nico`/`neco`: those are two people.
- **`_PARTICLES`** — surname particles (`le`, `de`, `van`, `der`, …) are no longer mistaken for forenames. `Le Fée` was unmatchable because `tokens()` yields `('le', 'fee')`, so `le` became the only forename on offer and two characters can never satisfy `_MIN_FORENAME_CHARS`.

Applied to the live corpus via `scripts/rematch_stances.py`: **8 gained, 0 cleared, 0 re-attributed**, and no recorded outcome disturbed. Small, and exactly the size predicted before writing it.

## The rule to carry forward

The unmatched rate is not a defect to be driven down. It is mostly correct behaviour: players outside the league, and honest abstentions on genuine ambiguity. Improve matching by adding **knowledge** — a name that is another name, a token that is not a forename — never by loosening a **threshold**.

If someone wants to reopen this, re-run the bucketing above first. The numbers, not the intuition, are the argument.
