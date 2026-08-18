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


class Creator(BaseModel):
    id: int
    name: str
    x_handle: str
    trust_tier: str


class CreatorStanceView(BaseModel):
    creator_name: str
    trust_tier: str
    stance: str
    confidence: float
    reason: str


class CreatorOpinions(BaseModel):
    player_name: str
    # Issue 06: sorted high trust tier first -- higher-trust opinions should
    # weigh more heavily in the synthesis a Reasoning implementation builds
    # from this.
    stances: list[CreatorStanceView]


class SemanticCreatorMatch(BaseModel):
    # Issue 09: a pgvector nearest-neighbour hit, resolved back to its
    # Creator Stance's structured fields (ADR-0005) -- not just raw text.
    creator_name: str
    trust_tier: str
    stance: str
    confidence: float
    reason: str
    matched_text: str


class SemanticCreatorOpinions(BaseModel):
    query: str
    # Issue 09: ordered by embedding similarity (closest first), unlike
    # CreatorOpinions above which sorts by trust tier.
    matches: list[SemanticCreatorMatch]


class PricePrediction(BaseModel):
    player_id: int
    player_name: str
    direction: str  # "rise" | "fall"
    ownership: float | None = None
    net_transfers_event: int
    evidence: str


class PriceChangePredictions(BaseModel):
    # Issue 08: each list sorted most-likely-first.
    rising: list[PricePrediction]
    falling: list[PricePrediction]


class Instinct(BaseModel):
    text: str
    logged_at: str


class Instincts(BaseModel):
    # Issue 13: always scoped to the current gameweek only -- never a
    # cross-gameweek history.
    notes: list[Instinct]
