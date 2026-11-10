"""Issue 19: how many trusted creators are on a player, and — the part that
actually matters — how that changed since last gameweek.

Two rules in `preferences.md` ask for exactly this and had no data behind
them:

    Treat creator/content opinions — including this AI's own — as input to
    weigh, never as the decision itself.

    Before finalizing a transfer near a deadline, check it against whatever
    was first considered earlier in the week; if the pick changed, be able to
    say why — genuine reassessment vs. bandwagon influence.

Three decisions shape everything below.

**Count creators, not Stances.** One prolific account posting "buy Semenyo"
six times is one creator's opinion. Counting rows would render that as six,
which is the precise illusion this tool exists to dispel — and it is not
hypothetical: `get_creator_opinions` measured 8 of 8 returned Stances for one
player coming from a single account, which is why it now caps per creator.
That cap only stopped one voice *looking* like several in a list; here the
number itself is the output, so distinct creators is the only honest unit.

**The derivative is the signal, not the level.** A player 11 of 19 creators
have liked all season is a template pick. A player who went 2 → 10 in four
days is a bandwagon forming. Both read as "10 of 19 are positive" and they
mean opposite things, so `movement` is computed and stated rather than left
for a reader to infer from two numbers.

**Attribution comes from the deadline, never from `creator_stances.gameweek`.**
That column is a snapshot taken once at the start of an ingestion run: 591
Stances from the 2026-08-27 batch are tagged GW1 regardless of what they are
about, because the run began before `fpl-ingest` advanced the state. Nothing
had ever filtered on it, so it had been harmless — a week-over-week count is
the first thing that would be silently corrupted by it. The stored tag is used
only as a last-resort fallback when no calendar exists at all, and the result
says so.

Deliberately NOT here:

- **A score.** Consensus is evidence *about* evidence. A high count is as
  often a warning as an endorsement, and turning it into a number the
  reasoning layer optimises would quietly make this app a bandwagon detector
  that rides bandwagons.
- **Citations.** A count is an aggregate, like an xG total; it belongs on an
  uncited evidence line. `get_creator_opinions` remains the citable path
  (ADR-0014), and a Stance still cannot become a pick (ADR-0004).
"""

from collections import defaultdict
from datetime import datetime, timezone
from typing import Literal

# Placeholder for an undated Stance's sort key. Timezone-aware so it is
# comparable with a real published_at even if the guard flag ahead of it in
# the sort key ever stops short-circuiting.
_NO_DATE = datetime.min.replace(tzinfo=timezone.utc)

# The stance vocabulary, split by what it implies about owning the player.
# `hold` is genuinely neutral -- it is the stance of someone who already owns
# them and is not moving, which is neither a buy signal nor a sell one.
POSITIVE_STANCES = frozenset({"buy", "captain", "differential"})
NEGATIVE_STANCES = frozenset({"sell", "avoid", "bench"})
NEUTRAL_STANCES = frozenset({"hold"})

# A swing this many distinct creators wide is not noise on a 19-creator
# roster -- it is a sixth of the roster changing its mind inside a week.
_SHARP_SWING = 3
# From a near-silent base, a smaller absolute move is still a sharp one:
# 1 -> 3 is a tripling, 8 -> 10 is not.
_SWING_FROM_QUIET = 2
_QUIET = 1
# Below this, "consensus" is a word for three people. Reported as counts
# either way, but never described as a majority view.
_MEANINGFUL_MAJORITY = 3

MovementKind = Literal[
    "no_coverage",
    "new_coverage",
    "dropped_off",
    "surging",
    "cooling",
    "settled",
    "steady",
]


def polarity(stance: str) -> str:
    """"positive" | "negative" | "neutral" for one stance kind."""
    if stance in POSITIVE_STANCES:
        return "positive"
    if stance in NEGATIVE_STANCES:
        return "negative"
    return "neutral"


def _latest_position_per_creator(rows: list[dict]) -> dict[str, str]:
    """Each creator's single position in this window: the polarity of their
    most recent Stance in it.

    A creator who said "hold" on Monday and "buy" on Thursday has one opinion,
    not two, and it is Thursday's. Without this the headline counts would
    double-count anyone who changed their mind mid-week -- and someone
    changing their mind mid-week is exactly the population this tool is
    watching.

    Ordering is on published_at; an undated Stance loses to any dated one
    rather than winning by accident of row order.
    """
    latest: dict[str, tuple[tuple, str]] = {}
    for row in rows:
        name = row["creator_name"]
        published = row["published_at"]
        # (dated?, when, id): the leading flag means an undated Stance can
        # never outrank a dated one, and -- because tuple comparison stops at
        # the first differing element -- a naive placeholder is never compared
        # against a timezone-aware timestamp. Ties fall back to the row id, so
        # the ordering is total and the result is not row-order dependent.
        key = (published is not None, published or _NO_DATE, row["stance_id"])
        current = latest.get(name)
        if current is None or key > current[0]:
            latest[name] = (key, polarity(row["stance"]))
    return {name: value[1] for name, value in latest.items()}


def summarise_window(gameweek: int | None, rows: list[dict]) -> dict:
    """One gameweek's counts. `rows` are already filtered to that gameweek."""
    by_stance: dict[str, set[str]] = defaultdict(set)
    stance_totals: dict[str, int] = defaultdict(int)
    for row in rows:
        by_stance[row["stance"]].add(row["creator_name"])
        stance_totals[row["stance"]] += 1

    positions = _latest_position_per_creator(rows)
    covering = sorted(positions)

    return {
        "gameweek": gameweek,
        "creators_covering": len(covering),
        "creators_positive": sum(1 for p in positions.values() if p == "positive"),
        "creators_negative": sum(1 for p in positions.values() if p == "negative"),
        "creators_neutral": sum(1 for p in positions.values() if p == "neutral"),
        "by_stance": [
            {
                "stance": stance,
                "creators": len(names),
                "stances": stance_totals[stance],
            }
            for stance, names in sorted(
                by_stance.items(), key=lambda item: (-len(item[1]), item[0])
            )
        ],
        "creator_names": covering,
        "stances": len(rows),
    }


def classify_movement(current: dict, previous: dict) -> MovementKind:
    """Which of the seven shapes this player's coverage is in.

    Checked swing-first: a sharp move is the interesting fact even when both
    endpoints happen to be majorities, and calling that "settled" would bury
    precisely what the user asked to be shown.
    """
    cur_cov, prev_cov = current["creators_covering"], previous["creators_covering"]
    if cur_cov == 0 and prev_cov == 0:
        return "no_coverage"
    # A rise from zero coverage is not a swing in opinion -- nobody changed
    # their mind, the player simply entered the conversation. Reporting it as
    # a bandwagon would cry wolf on every newly-promoted differential, and
    # the point of this tool is to be believed when it does say bandwagon.
    if prev_cov == 0:
        return "new_coverage"
    if cur_cov == 0:
        return "dropped_off"

    cur_pos, prev_pos = current["creators_positive"], previous["creators_positive"]
    delta = cur_pos - prev_pos
    if delta >= _SHARP_SWING or (delta >= _SWING_FROM_QUIET and prev_pos <= _QUIET):
        return "surging"
    if delta <= -_SHARP_SWING or (delta <= -_SWING_FROM_QUIET and cur_pos <= _QUIET):
        return "cooling"
    if (
        cur_pos >= _MEANINGFUL_MAJORITY
        and prev_pos >= _MEANINGFUL_MAJORITY
        and abs(delta) <= 1
    ):
        return "settled"
    return "steady"


def _are(count: int) -> str:
    """"is" or "are". These sentences are written to be quoted verbatim by the
    reasoning layer and read by the user, so "1 trusted creators are" is a
    visible defect, not a nitpick."""
    return "is" if count == 1 else "are"


def _creators(count: int) -> str:
    return "trusted creator" if count == 1 else "trusted creators"


def describe(
    player_name: str,
    movement: MovementKind,
    current: dict,
    previous: dict,
    roster_size: int,
) -> str:
    """The sentence the reasoning layer actually reads.

    Ticket 18 established that a number the model has to interpret gets
    interpreted inconsistently, and a phrase it can quote does not. The same
    applies here, with more force: "9 and 2" is inert, "up from 2 -- days old,
    not a settled view" changes what the model writes in its risks.
    """
    cur_pos = current["creators_positive"]
    prev_pos = previous["creators_positive"]
    cur_gw = current["gameweek"]
    prev_gw = previous["gameweek"]
    here = f"GW{cur_gw}" if cur_gw is not None else "this gameweek"
    before = f"GW{prev_gw}" if prev_gw is not None else "last gameweek"

    if movement == "no_coverage":
        return (
            f"No trusted creator has taken a position on {player_name} in {before} "
            f"or {here}. That is an absence of coverage, not agreement -- it says "
            "nothing either way about whether the move is right."
        )
    if movement == "new_coverage":
        return (
            f"{cur_pos} of {roster_size} trusted creators {_are(cur_pos)} positive on "
            f"{player_name} for {here}, from no coverage at all in {before}. "
            "Nobody changed their mind -- they have just entered the conversation, "
            "so this is new attention rather than a measurable swing."
        )
    if movement == "dropped_off":
        return (
            f"{previous['creators_covering']} "
            f"{_creators(previous['creators_covering'])} "
            f"{'was' if previous['creators_covering'] == 1 else 'were'} talking about "
            f"{player_name} in {before} and none are in {here}. Interest has moved "
            "on; the older takes are still on record but nobody is restating them."
        )
    if movement == "surging":
        return (
            f"{cur_pos} of {roster_size} trusted creators {_are(cur_pos)} positive on "
            f"{player_name} for {here}, up from {prev_pos} in {before}. That is a "
            "sharp swing inside a week, not a long-held view -- a consensus this "
            "fast-moving is a reason to check your own reasoning, not confirmation "
            "of it."
        )
    if movement == "cooling":
        return (
            f"Positive coverage of {player_name} has fallen from {prev_pos} of "
            f"{roster_size} trusted creators in {before} to {cur_pos} in {here}. "
            "Something changed the picture -- find out what before acting on the "
            "older takes."
        )
    if movement == "settled":
        return (
            f"{cur_pos} of {roster_size} trusted creators {_are(cur_pos)} positive on "
            f"{player_name} for {here}, against {prev_pos} in {before} -- a view "
            "held steadily across gameweeks rather than a recent swing. Heavy "
            "settled ownership makes NOT owning them the riskier position."
        )
    return (
        f"{cur_pos} of {roster_size} trusted creators {_are(cur_pos)} positive on "
        f"{player_name} for {here}, against {prev_pos} in {before} -- no meaningful movement "
        "either way."
    )


def instinct_note(
    player_name: str, movement: MovementKind, current: dict, previous: dict
) -> str:
    """What to say when the user has a live Instinct naming this player.

    Ticket 13 logged the user's early-week thinking so it would survive to
    deadline day. This is the other half: what changed in the world between
    those two moments. The app's job is to make the influence visible and then
    stop -- `preferences.md` is explicit that the final call is the user's, so
    this states the fact and asks the question rather than ruling on it.
    """
    delta = current["creators_positive"] - previous["creators_positive"]
    if movement == "surging":
        change = (
            f"creator positivity on {player_name} has gone "
            f"{previous['creators_positive']} -> {current['creators_positive']} since"
        )
    elif movement == "new_coverage":
        change = f"{player_name} has gone from no creator coverage at all to some since"
    elif movement == "cooling":
        change = (
            f"creator positivity on {player_name} has fallen "
            f"{previous['creators_positive']} -> {current['creators_positive']} since"
        )
    elif delta == 0:
        change = f"creator opinion on {player_name} has not moved since"
    else:
        change = (
            f"creator positivity on {player_name} has moved "
            f"{previous['creators_positive']} -> {current['creators_positive']} since"
        )
    return (
        f"The user logged an Instinct naming {player_name} earlier this gameweek, and "
        f"{change}. Put both in front of them: say what has changed and ask whether "
        "their thinking has genuinely evolved or whether they are following the move. "
        "Do not silently override the Instinct and do not silently agree with it."
    )
