from psycopg import Connection

from fpl_coach.models import Creator

VALID_TRUST_TIERS = {"high", "medium", "low"}


def add_creator(conn: Connection, name: str, x_handle: str, trust_tier: str) -> Creator:
    """Adds a creator with a manually assigned trust tier (ADR-0001). Adding
    the same x_handle again updates its name/tier rather than erroring, so
    re-running the CLI to fix a typo or re-tier a creator is safe."""
    if trust_tier not in VALID_TRUST_TIERS:
        raise ValueError(f"trust_tier must be one of {sorted(VALID_TRUST_TIERS)}, got {trust_tier!r}")

    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO creators (name, x_handle, trust_tier) VALUES (%s, %s, %s) "
            "ON CONFLICT (x_handle) DO UPDATE SET name = EXCLUDED.name, "
            "trust_tier = EXCLUDED.trust_tier "
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
