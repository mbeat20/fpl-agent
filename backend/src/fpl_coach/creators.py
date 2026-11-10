from dataclasses import dataclass

from psycopg import Connection

from fpl_coach.models import Creator

VALID_TRUST_TIERS = {"high", "medium", "low"}
VALID_PLATFORMS = {"x", "youtube"}


@dataclass(frozen=True)
class CreatorSource:
    id: int
    creator_id: int
    platform: str
    external_id: str
    handle: str | None


def add_creator(
    conn: Connection, name: str, x_handle: str | None, trust_tier: str
) -> Creator:
    """Adds a creator with a manually assigned trust tier (ADR-0001). Adding
    the same creator again updates its details rather than erroring, so
    re-running the CLI to fix a typo or re-tier a creator is safe.

    Issue 16: x_handle is optional, because a creator can now be followed on
    YouTube alone (see creator_sources), and that breaks a single ON CONFLICT
    target. `ON CONFLICT (x_handle)` never fires for NULL, so a handle-less
    creator would be inserted twice; but keying on `name` instead would stop
    ticket 06's rename-by-handle from working. Worse, giving a handle-less
    creator an X handle later conflicts on `name` while the statement targets
    `x_handle`, which is a plain UniqueViolation.

    So the existing row is looked up explicitly first. Slower by one query,
    and correct for all four combinations.
    """
    if trust_tier not in VALID_TRUST_TIERS:
        raise ValueError(f"trust_tier must be one of {sorted(VALID_TRUST_TIERS)}, got {trust_tier!r}")

    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, name, x_handle FROM creators WHERE x_handle = %s OR name = %s",
            (x_handle, name),
        )
        existing = cur.fetchall()

    # Two different creators already hold this name and this handle. Silently
    # picking one would merge two identities in the trust record.
    if len(existing) > 1:
        holders = ", ".join(f"{row['name']} (@{row['x_handle']})" for row in existing)
        raise ValueError(
            f"{name!r}/@{x_handle} matches more than one existing creator: {holders}. "
            "Rename or remove one of them first."
        )

    with conn.cursor() as cur:
        if existing:
            cur.execute(
                "UPDATE creators SET name = %s, "
                "x_handle = COALESCE(%s, x_handle), trust_tier = %s "
                "WHERE id = %s RETURNING id, name, x_handle, trust_tier",
                (name, x_handle, trust_tier, existing[0]["id"]),
            )
        else:
            cur.execute(
                "INSERT INTO creators (name, x_handle, trust_tier) VALUES (%s, %s, %s) "
                "RETURNING id, name, x_handle, trust_tier",
                (name, x_handle, trust_tier),
            )
        row = cur.fetchone()
    conn.commit()
    return Creator(**row)


def list_creators(conn: Connection) -> list[Creator]:
    with conn.cursor() as cur:
        cur.execute("SELECT id, name, x_handle, trust_tier FROM creators ORDER BY name")
        rows = cur.fetchall()
    return [Creator(**row) for row in rows]


def add_creator_source(
    conn: Connection, creator_id: int, platform: str, external_id: str, handle: str | None = None
) -> CreatorSource:
    """Attaches a platform identity to a creator. external_id is the
    platform's own stable id (a UC... channel id for YouTube), not the
    handle -- handles get renamed, ids do not.

    Re-adding the same (platform, external_id) updates which creator it
    belongs to rather than erroring, so a mis-attached channel can be fixed
    by re-running the CLI. The UNIQUE constraint still stops one channel
    being counted twice under two creators, which would inflate ticket 19's
    consensus.
    """
    if platform not in VALID_PLATFORMS:
        raise ValueError(f"platform must be one of {sorted(VALID_PLATFORMS)}, got {platform!r}")

    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO creator_sources (creator_id, platform, external_id, handle) "
            "VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (platform, external_id) DO UPDATE SET "
            "creator_id = EXCLUDED.creator_id, handle = EXCLUDED.handle "
            "RETURNING id, creator_id, platform, external_id, handle",
            (creator_id, platform, external_id, handle),
        )
        row = cur.fetchone()
    conn.commit()
    return CreatorSource(**row)


def list_creator_sources(
    conn: Connection, platform: str | None = None, creator_ids: list[int] | None = None
) -> list[CreatorSource]:
    clauses, params = [], []
    if platform is not None:
        clauses.append("platform = %s")
        params.append(platform)
    if creator_ids is not None:
        clauses.append("creator_id = ANY(%s)")
        params.append(creator_ids)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, creator_id, platform, external_id, handle FROM creator_sources "
            f"{where} ORDER BY creator_id, platform",
            params,
        )
        rows = cur.fetchall()
    return [CreatorSource(**row) for row in rows]
