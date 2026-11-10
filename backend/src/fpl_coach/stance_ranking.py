"""Issue 18: one answer to "how much does this Creator Stance still count",
shared by get_creator_opinions and search_creator_opinions.

Before this, a Stance was timeless. `get_creator_opinions` filtered on player
name with no gameweek filter and no LIMIT, so a pre-season take and a
deadline-eve take arrived in the same undifferentiated list -- measured on
the live database, 233 of 1,342 Stances predated the season entirely, eight
of them from 2022. The loudest historical week won.

Two things decide a Stance's weight, and both must be visible in the tool
result rather than only implied by list order:

- **Recency.** FPL opinion has a short half-life: a captaincy call is about
  one specific set of fixtures, and is worthless once they have been played.
  Decay is exponential with a one-gameweek half-life rather than a hard
  cutoff, because ADR-0012's outcome history and ticket 19's week-over-week
  consensus both need the old rows to keep existing (the ticket is explicit:
  prefer decay to deletion, delete nothing).
- **Trust tier.** ADR-0001 chose manual tiers for v1. The roster is now 12
  `high` and 7 `medium`, so the tier is a real signal and is applied here as
  a multiplier -- not the no-op sort it was when every creator was `high`.

Ranking on `published_at`, never `fetched_at` or `created_at`: those record
when *we* saw something, which for a backfilled five-month-old post is
today.
"""

from datetime import datetime, timezone

# ADR-0001's manual tiers, as multipliers. A medium-trust creator saying
# something today should still outrank a high-trust creator from last
# gameweek -- the gap between tiers is deliberately smaller than a
# half-life's worth of decay.
_TIER_WEIGHT = {"high": 1.0, "medium": 0.7, "low": 0.4}
_UNKNOWN_TIER_WEIGHT = 0.4

# One gameweek. A Stance from the previous gameweek is worth about half a
# Stance from this one, which is the intended "visibly older and weighs
# less" rather than "gone".
HALF_LIFE_DAYS = 7.0

# What a single tool call returns. Bounded so a player with 57 historical
# Stances cannot flood the context; the full match count is reported
# alongside so a truncated view is never mistaken for the whole picture.
DEFAULT_LIMIT = 8

# A Stance with no date at all cannot be ranked honestly. It is not dropped
# (that would silently hide content), but it sits below anything dated from
# the last fortnight rather than competing with today's opinion.
_UNDATED_RECENCY = 0.25


def age_days(published_at: datetime | None, now: datetime | None = None) -> float | None:
    """Age in days, or None for an undated Stance."""
    if published_at is None:
        return None
    now = now or datetime.now(timezone.utc)
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)
    return max((now - published_at).total_seconds() / 86400.0, 0.0)


def recency_weight(published_at: datetime | None, now: datetime | None = None) -> float:
    """Exponential decay on age, halving every HALF_LIFE_DAYS."""
    days = age_days(published_at, now)
    if days is None:
        return _UNDATED_RECENCY
    return 0.5 ** (days / HALF_LIFE_DAYS)


def stance_weight(
    trust_tier: str | None, published_at: datetime | None, now: datetime | None = None
) -> float:
    """The number both tools sort on: trust tier scaled by recency decay."""
    tier = _TIER_WEIGHT.get(trust_tier or "", _UNKNOWN_TIER_WEIGHT)
    return tier * recency_weight(published_at, now)


def is_current(gameweek: int | None, planning_gameweek: int | None) -> bool:
    """True when a Stance was published for the gameweek still being planned
    -- the only ones that are straightforwardly about a decision the user can
    still act on."""
    if gameweek is None or planning_gameweek is None:
        return False
    return gameweek >= planning_gameweek


def describe_recency(
    published_at: datetime | None,
    gameweek: int | None,
    planning_gameweek: int | None,
    now: datetime | None = None,
) -> str:
    """A short phrase the reasoning layer can read and quote, so "this take is
    from before the last deadline" is something the model is told rather than
    something it has to infer from a raw timestamp.

    This is the load-bearing half of the ticket's contradictory-pair
    requirement: when an old 'buy' and a new 'sell' both come back, the model
    can only prefer the new one if the difference is stated in words it
    actually reads.
    """
    days = age_days(published_at, now)
    if days is None:
        when = "date unknown"
    elif days < 1:
        when = "today"
    elif days < 2:
        when = "yesterday"
    else:
        when = f"{int(days)} days ago"

    if gameweek is None:
        return f"{when}, gameweek unknown"
    if planning_gameweek is None:
        return f"{when}, published for GW{gameweek}"
    if gameweek >= planning_gameweek:
        return f"{when}, for GW{gameweek} -- the gameweek being planned"
    if gameweek == planning_gameweek - 1:
        return (
            f"{when}, for GW{gameweek} -- already played, this predates "
            f"the GW{planning_gameweek} deadline"
        )
    return (
        f"{when}, for GW{gameweek} -- {planning_gameweek - gameweek} gameweeks stale, "
        "almost certainly about circumstances that have since changed"
    )
