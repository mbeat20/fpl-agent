"""One-off backfill: re-resolve every Creator Stance's `fpl_player_id` with
the current matcher.

Why a script and not a migration: matching is code, not schema, and this needs
to be re-runnable. Any future improvement to `player_names.match_player_name`
leaves the same gap behind it -- rows resolved (or not) under the old rules --
for exactly the reason the ingestion-backfill pattern exists: derive the work
from "rows whose stored answer disagrees with what the matcher says now",
never from what happened to change in the current run.

Three kinds of change, and the third is the one to look at before saying yes:

  gained        -- was NULL, now resolves. The point of the exercise.
  cleared       -- was resolved, now abstains. Mostly honest corrections: the
                   old lookup answered an ambiguous surname with
                   `ILIKE %name% ORDER BY name LIMIT 1`, so ten "Palmer"
                   stances were split between Cole and Alex by sort order.
  re-attributed -- resolved to a different player. Rare and worth reading
                   individually.

`outcome_*` is cleared on any row whose player changes or is cleared. Ticket
24 scores a Stance against what that player actually did, so an outcome
carried over from a coin-flip attribution is a wrong number in the accuracy
history -- worse than an unresolved one, which ticket 24 will simply
re-resolve on its next run.

    uv run python scripts/rematch_stances.py            # dry run, prints a diff
    uv run python scripts/rematch_stances.py --apply    # writes
"""

import os
import sys
from collections import Counter

from dotenv import load_dotenv

from fpl_coach.db import get_connection
from fpl_coach.player_names import match_player_name
from fpl_coach.tools.get_player import player_pool


def main() -> None:
    load_dotenv()
    apply = "--apply" in sys.argv
    sys.stdout.reconfigure(encoding="utf-8")

    with get_connection() as conn:
        pool = player_pool(conn)
        if not pool:
            raise SystemExit(
                "fpl_players is empty -- run fpl-ingest first. Re-matching every "
                "stance against an empty pool would clear every attribution."
            )
        web_names = {pid: name for pid, name, _ in pool}

        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, player_name, fpl_player_id, outcome_points "
                "FROM creator_stances WHERE player_name IS NOT NULL"
            )
            rows = cur.fetchall()

        gained, cleared, reattributed = [], [], []
        for row in rows:
            new = match_player_name(row["player_name"], pool)
            old = row["fpl_player_id"]
            if new == old:
                continue
            bucket = gained if old is None else (cleared if new is None else reattributed)
            bucket.append((row["id"], row["player_name"], old, new, row["outcome_points"]))

        _report("GAINED", gained, web_names)
        _report("CLEARED", cleared, web_names)
        _report("RE-ATTRIBUTED", reattributed, web_names)

        changed = gained + cleared + reattributed
        matched_before = sum(1 for r in rows if r["fpl_player_id"] is not None)
        matched_after = matched_before + len(gained) - len(cleared)
        print(
            f"\n{len(rows)} player-named stances: "
            f"{matched_before} matched ({matched_before / len(rows):.0%}) -> "
            f"{matched_after} ({matched_after / len(rows):.0%}); "
            f"{len(changed)} rows would change."
        )
        # An outcome is only meaningful for the player it was scored against,
        # so it survives a gain (there was no player before, so no outcome
        # could have been recorded) but never a clear or a re-attribution.
        drops_outcome = sum(1 for _, _, _, _, pts in cleared + reattributed if pts is not None)
        print(f"{drops_outcome} of them carry a recorded outcome that will be cleared with it.")

        if not apply:
            print("\nDry run. Re-run with --apply to write.")
            return

        with conn.cursor() as cur:
            for stance_id, _, _, new, _ in gained:
                cur.execute(
                    "UPDATE creator_stances SET fpl_player_id = %s WHERE id = %s",
                    (new, stance_id),
                )
            for stance_id, _, _, new, _ in cleared + reattributed:
                cur.execute(
                    "UPDATE creator_stances SET fpl_player_id = %s, outcome_points = NULL, "
                    "outcome_price_change = NULL, outcome_recorded_at = NULL WHERE id = %s",
                    (new, stance_id),
                )
        conn.commit()
        print(f"\nApplied. {len(changed)} rows updated.")


def _report(label: str, items: list, web_names: dict[int, str]) -> None:
    print(f"\n--- {label} ({len(items)}) ---")
    counts = Counter(
        f"{name!r}: {web_names.get(old, '-')!r} -> {web_names.get(new, '-')!r}"
        for _, name, old, new, _ in items
    )
    for line, n in counts.most_common(30):
        print(f"  x{n:<4} {line}")
    if len(counts) > 30:
        print(f"  ... and {len(counts) - 30} more distinct names")


if __name__ == "__main__":
    main()
