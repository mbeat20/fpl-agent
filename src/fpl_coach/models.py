from pydantic import BaseModel


class Player(BaseModel):
    id: int
    name: str
    position: str
    club: str
    price: float
    # Issue 03: populated by real FPL API ingestion; absent for manually seeded rows.
    ownership: float | None = None
    form: float | None = None
    status: str | None = None
    news: str | None = None
    # Issue 05: Understat xG/xA, matched in by name; NULL when unmatched or
    # not yet ingested.
    xg: float | None = None
    xa: float | None = None


class Team(BaseModel):
    id: int
    name: str
    players: list[Player]


class PlayerComparison(BaseModel):
    players: list[Player]


class PlayerRisk(BaseModel):
    player_id: int
    player_name: str
    status: str | None = None
    news: str | None = None
    # Issue 07: the club's next ingested fixture, null if none is scheduled
    # (a blank gameweek) or the club has no ingested fixtures at all.
    next_opponent: str | None = None
    next_fixture_difficulty: int | None = None
    injury_risk: bool
    fixture_risk: bool


class SquadRiskSummary(BaseModel):
    players: list[PlayerRisk]


class RecommendationPick(BaseModel):
    player_id: int
    player_name: str


class Alternative(BaseModel):
    player_id: int
    player_name: str
    reason: str


class Recommendation(BaseModel):
    pick: RecommendationPick
    confidence: float
    evidence: list[str]
    risks: list[str]
    alternative: Alternative
