"""Ticket 29: bring `creator_stances.subject` rows already stored onto the
canonical spelling `normalise_subject` now applies at extraction time.

Idempotent, and derived from what disagrees with the normaliser now rather
than from what any particular run wrote -- so a later addition to the chip
vocabulary or the club alias list is picked up by re-running this, the same
shape as `rematch_stances.py`.

    uv run python scripts/normalise_subjects.py            # dry run
    uv run python scripts/normalise_subjects.py --apply
"""

import sys
from collections import Counter

from dotenv import load_dotenv

from fpl_coach.db import get_connection
from fpl_coach.subjects import club_names, normalise_subject


def main() -> None:
    load_dotenv()
    apply = "--apply" in sys.argv
    sys.stdout.reconfigure(encoding="utf-8")

    with get_connection() as conn:
        clubs = club_names(conn)
        if not clubs:
            raise SystemExit(
                "clubs is empty -- run fpl-ingest first. Without it every club "
                "subject would be left unnormalised and the run would look clean."
            )

        with conn.cursor() as cur:
            cur.execute(
                "SELECT subject, count(*) AS n FROM creator_stances "
                "WHERE subject IS NOT NULL GROUP BY subject ORDER BY n DESC"
            )
            rows = cur.fetchall()

        changes = [
            (row["subject"], normalise_subject(row["subject"], clubs), row["n"])
            for row in rows
        ]
        changes = [(old, new, n) for old, new, n in changes if new != old]

        after = Counter()
        for row in rows:
            after[normalise_subject(row["subject"], clubs)] += row["n"]

        print(f"distinct subjects: {len(rows)} -> {len(after)}")
        print(f"\n--- would change ({sum(n for _, _, n in changes)} rows) ---")
        for old, new, n in sorted(changes, key=lambda c: -c[2]):
            print(f"  x{n:<4} {old!r} -> {new!r}")

        print("\n--- after, by volume ---")
        for subject, n in after.most_common(12):
            print(f"  {n:>4}  {subject!r}")

        if not apply:
            print("\nDry run. Re-run with --apply to write.")
            return

        with conn.cursor() as cur:
            for old, new, _ in changes:
                cur.execute(
                    "UPDATE creator_stances SET subject = %s WHERE subject = %s", (new, old)
                )
        conn.commit()
        print(f"\nApplied. {sum(n for _, _, n in changes)} rows renamed.")


if __name__ == "__main__":
    main()
