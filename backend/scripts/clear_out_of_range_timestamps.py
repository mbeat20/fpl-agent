"""One-off cleanup for the deep links ticket 17's boundary check now rejects.

`in_range_start_seconds` stops new out-of-range values at ingestion; this
clears the ones already stored. Same shape as `rematch_stances.py`: derived
from what is wrong in the table now, not from what a particular run did, so
it stays correct if run again.

Content chunks carry a `start_seconds` too, recovered from the transcript's
own `[MM:SS]` markers rather than authored by the model, so they are checked
as well -- if any are out of range the marker parser is at fault, which is a
different bug worth knowing about.

    uv run python scripts/clear_out_of_range_timestamps.py            # dry run
    uv run python scripts/clear_out_of_range_timestamps.py --apply
"""

import sys

from dotenv import load_dotenv

from fpl_coach.db import get_connection

_OUT_OF_RANGE = """
SELECT s.id, s.player_name, s.start_seconds, cc.duration_seconds, cc.source_url
FROM creator_stances s
JOIN creator_content cc ON cc.id = s.source_content_id
WHERE s.start_seconds IS NOT NULL
  AND (s.start_seconds < 0
       OR (cc.duration_seconds IS NOT NULL AND s.start_seconds > cc.duration_seconds))
ORDER BY s.start_seconds DESC
"""

_CHUNKS_OUT_OF_RANGE = """
SELECT count(*) AS n
FROM content_chunks ch
JOIN creator_content cc ON cc.id = ch.content_id
WHERE ch.start_seconds IS NOT NULL
  AND (ch.start_seconds < 0
       OR (cc.duration_seconds IS NOT NULL AND ch.start_seconds > cc.duration_seconds))
"""


def main() -> None:
    load_dotenv()
    apply = "--apply" in sys.argv
    sys.stdout.reconfigure(encoding="utf-8")

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(_OUT_OF_RANGE)
            rows = cur.fetchall()
            cur.execute("SELECT count(*) AS n FROM creator_stances WHERE start_seconds IS NOT NULL")
            total = cur.fetchone()["n"]
            cur.execute(_CHUNKS_OUT_OF_RANGE)
            bad_chunks = cur.fetchone()["n"]

        print(f"{len(rows)} of {total} timestamped stances point outside their own content.")
        for row in rows[:15]:
            print(
                f"  {row['source_url']}&t={row['start_seconds']}s "
                f"(video is {row['duration_seconds']}s) -- {row['player_name']}"
            )
        if len(rows) > 15:
            print(f"  ... and {len(rows) - 15} more")
        print(
            f"\ncontent_chunks out of range: {bad_chunks} "
            "(should be 0 -- those come from the transcript's own markers, not the model)"
        )

        if not apply:
            print("\nDry run. Re-run with --apply to clear them.")
            return

        with conn.cursor() as cur:
            cur.execute(
                "UPDATE creator_stances SET start_seconds = NULL WHERE id = ANY(%s)",
                ([row["id"] for row in rows],),
            )
        conn.commit()
        print(f"\nCleared {len(rows)} start_seconds. Those citations now open the video at 0:00.")


if __name__ == "__main__":
    main()
