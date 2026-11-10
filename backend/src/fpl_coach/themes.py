"""Issue 29: what creators are collectively talking *about*.

Ticket 28 answers "which players". This answers the question underneath it --
the Arsenal rotation nobody is pricing in, the case for wildcarding now -- the
thing a creator's audience actually takes away.

The obvious place to look is `creator_stances.subject`, the field for a Stance
about no player, and it is a dead end: measured 2026-08-30, **11 non-player
stances out of 1,605**, across 9 values, all chip names and case-fragmented.
There is no theme corpus to read.

There is one to *derive*. `content_chunks.embedding` (pgvector, 1024-dim,
ADR-0005) was built for ticket 09's semantic search and used for nothing else.
Themes are what those embeddings look like when you stop querying them one
question at a time.

**Clustered by cross-creator density, not by k-means.** For each chunk, count
the distinct CREATORS among its near neighbours. A chunk whose neighbourhood
is one creator is that creator repeating themselves; a chunk whose
neighbourhood spans seven creators is a theme. That needs no new dependency --
pgvector computes the distances, and the clustering is a short greedy pass.

**Labelled by the medoid: a real sentence a real creator actually said.** This
is the part that matters. A generated label is exactly the hallucination
surface ADR-0014 was written to close -- plausible, unverifiable, and wrong in
a way that looks authoritative. A medoid chunk is a quotation with a
`source_url` and a `start_seconds`, so the label is checkable and clickable.

**The players come free.** `content_chunks -> creator_content ->
creator_stances` is already joined, so a cluster's stances hand back the
players it is about with no text matching.

The threshold is measured, not guessed. On the cleaned corpus (ticket 30)
nearest-neighbour distance runs p05 0.205 / p25 0.258 / median 0.289, and pair
counts climb 44 / 219 / 1042 / 5220 at 0.20 / 0.25 / 0.30 / 0.35. 0.26 sits
where the count is still selective. Re-measure if the corpus shape changes --
in particular if YouTube transcripts start landing, because a 40-minute
transcript chunks very differently from a two-line X paraphrase.
"""

from collections import defaultdict
from datetime import datetime, timedelta, timezone

from psycopg import Connection

from fpl_coach.gameweek import planning_gameweek as resolve_planning_gameweek
from fpl_coach.models import Citation, Theme, ThemeBoard, deep_link
from fpl_coach.player_names import fold

# Cosine distance below which two chunks are "about the same thing". See the
# module docstring for the measurement this comes from.
DEFAULT_THRESHOLD = 0.26
# A theme has to be held by several people. Below this it is one creator
# repeating themselves, which is the thing cross-creator density exists to
# filter out -- and a "theme" of one is just a post.
MIN_CREATORS = 3
# How many of a cluster's content rows must name a player before it can appear
# in the cluster's TITLE. Two, for the same reason MIN_CREATORS is three: said
# once is a post, not a theme.
_MIN_TITLE_MENTIONS = 2
# How far back to look. A gameweek's discourse, roughly.
WINDOW_DAYS = 10
# Content newer than this counts as "new this week" rather than carried over.
RECENT_HOURS = 48
DEFAULT_LIMIT = 8
# How many players a theme names. A long transcript chunk mentions twenty, and
# listing all of them says nothing -- the theme is about the handful the
# cluster keeps coming back to, ranked by how many of its content rows mention
# them.
MAX_PLAYERS = 6

# Chunks that are not creator opinion at all.
#
# Ticket 30 fixed the parser that produced these and deduplicated the rows, but
# 23 legacy rows survive because they carry 36 RESOLVED OUTCOMES (ticket 24)
# that cannot be re-fetched. Their first chunk is Grok's own narration
# ("Here are the 5 most recent distinct posts from @..."), which is near
# identical across creators -- so it clusters beautifully and forms the single
# largest "theme", of Grok talking about itself. Verified: 9 creators, 13
# mentions, top of the board.
#
# Filtered at the chunk level rather than the row level on purpose: the later
# chunks of those rows are real paraphrases and still count.
_NARRATION = "Here are the%"

_SCOPE_SQL = """
SELECT ch.id, ch.content_id, ch.chunk_text, ch.start_seconds,
       cc.creator_id, cc.source_url, cc.published_at,
       cr.name AS creator_name, cr.trust_tier
FROM content_chunks ch
JOIN creator_content cc ON cc.id = ch.content_id
JOIN creators cr ON cr.id = cc.creator_id
WHERE ch.embedding IS NOT NULL
  AND ch.chunk_text NOT ILIKE %(narration)s
  AND (cc.published_at IS NULL OR cc.published_at >= %(since)s)
"""

# Every close pair among the scoped chunks. Computed by pgvector rather than in
# Python: 831 chunks is 345k pairwise comparisons of 1024-dim vectors, which is
# instant in C and minutes in a loop.
_EDGES_SQL = """
WITH scope AS (
    SELECT ch.id, ch.embedding
    FROM content_chunks ch
    JOIN creator_content cc ON cc.id = ch.content_id
    WHERE ch.embedding IS NOT NULL
      AND ch.chunk_text NOT ILIKE %(narration)s
      AND (cc.published_at IS NULL OR cc.published_at >= %(since)s)
)
SELECT a.id AS a_id, b.id AS b_id
FROM scope a
JOIN scope b ON b.id <> a.id
WHERE (a.embedding <=> b.embedding) < %(threshold)s
"""

_PLAYERS_SQL = """
SELECT source_content_id, player_name
FROM creator_stances
WHERE source_content_id = ANY(%s) AND player_name IS NOT NULL AND player_name <> ''
"""

# The same thing, restricted to stances that RESOLVED to a real FPL player.
#
# `players` on the panel is "the names creators said", and an unresolved name
# is often a real player whose name the auto-captions mangled, so excluding
# them there would lose real information. A TITLE is different: it is the
# panel's assertion about what a theme is, and it must not rest on an
# unverified string. Fed the unfiltered list, the distinctiveness ranking
# below headed live themes with "Chiao", "Chelsea defense" and "Arne Slot"
# (a manager) -- rare precisely because they are extraction noise.
_RESOLVED_PLAYERS_SQL = """
SELECT source_content_id, player_name
FROM creator_stances
WHERE source_content_id = ANY(%s) AND fpl_player_id IS NOT NULL
"""


def _neighbours(conn: Connection, since: datetime, threshold: float) -> dict[int, set[int]]:
    with conn.cursor() as cur:
        cur.execute(
            _EDGES_SQL,
            {"since": since, "threshold": threshold, "narration": _NARRATION},
        )
        edges = cur.fetchall()
    graph: dict[int, set[int]] = defaultdict(set)
    for edge in edges:
        graph[edge["a_id"]].add(edge["b_id"])
    return graph


def _cluster(
    chunks: dict[int, dict], graph: dict[int, set[int]], min_creators: int
) -> list[tuple[int, set[int]]]:
    """Greedy cross-creator density clustering.

    Repeatedly take the chunk whose neighbourhood spans the most distinct
    creators, claim it and its neighbours as a theme, remove them, and go
    again. Returns (seed_id, member_ids) per cluster.

    Greedy rather than k-means for two reasons that matter here: there is no
    sensible k for "how many things are people talking about this week", and
    the seed of a density cluster is a real chunk, which is what lets the
    label be a quotation instead of a centroid nobody said.
    """
    remaining = set(chunks)
    clusters: list[tuple[int, set[int]]] = []

    while remaining:
        best_id, best_creators, best_members = None, 0, set()
        for chunk_id in remaining:
            members = ({chunk_id} | graph.get(chunk_id, set())) & remaining
            creators = {chunks[m]["creator_id"] for m in members}
            if len(creators) > best_creators:
                best_id, best_creators, best_members = chunk_id, len(creators), members

        if best_id is None or best_creators < min_creators:
            break

        clusters.append((best_id, best_members))
        remaining -= best_members

    return clusters


def _citation(chunk: dict) -> Citation | None:
    """The medoid's own source, built the same way ticket 17 builds one -- from
    the stored row, never from anything a model wrote."""
    if not chunk["source_url"]:
        return None
    return Citation(
        creator_name=chunk["creator_name"],
        trust_tier=chunk["trust_tier"],
        url=deep_link(chunk["source_url"], chunk["start_seconds"]),
        start_seconds=chunk["start_seconds"],
        published_at=chunk["published_at"].isoformat() if chunk["published_at"] else None,
    )


# ---------------------------------------------------------------------------
# Naming a theme.
#
# The label used to be the theme's name, and a transcript sentence is not a
# name. Opened in a browser on 2026-09-04 the panel was headed by "Whereas
# with now, right, you have this high floor created for you by the variables
# discussed beforehand." -- correctly cited, entirely real, and no use as a
# heading. Choosing a better quote (see _representative) removed the
# mid-clause fragments but could not fix the underlying mismatch: a quotation
# is EVIDENCE, and evidence does not make a title.
#
# So a theme now carries both. `title` names it and `label` quotes it.
#
# The rule ADR-0014 actually imposes is that nothing on the panel may be prose
# a model wrote, because a plausible-but-unverifiable sentence is the whole
# hallucination surface. A title assembled by counting is not prose: every
# element of it is either a term from the closed vocabulary below, matched
# literally in the creators' own words, or a player name resolved through
# creator_stances. There is no model anywhere in it, and it degrades to a bare
# count rather than guessing when it recognises nothing -- STATUS.md §7.

# FPL's vocabulary, as a closed list. Each entry is (display name, the spellings
# to look for). Closed on purpose: an open-ended keyword extractor would start
# titling themes with whatever noun happened to be frequent, which is a worse
# version of the problem this replaces.
#
# Ordered most specific first, so "double gameweek" is not reported as
# "fixtures" and "triple captain" is not reported as "captaincy".
_TOPICS: list[tuple[str, tuple[str, ...]]] = [
    ("Wildcard timing", ("wildcard", "wild card")),
    ("Bench boost", ("bench boost", "benchboost")),
    ("Triple captain", ("triple captain", "triple captaincy")),
    ("Free hit", ("free hit", "freehit")),
    ("Assistant manager", ("assistant manager", "am chip")),
    ("Double gameweeks", ("double gameweek", "double game week", "dgw")),
    ("Blanks", ("blank gameweek", "blank game week", "bgw", "blanks")),
    ("Captaincy", ("captain", "armband", "captaincy")),
    ("Price changes", ("price rise", "price fall", "price change", "team value")),
    ("Injuries and fitness", ("injury", "injured", "knock", "doubt", "fitness", "flagged")),
    ("Rotation risk", ("rotation", "rotated", "rested", "minutes risk")),
    ("Fixture runs", ("fixture run", "fixtures", "run of games", "fdr")),
    ("Differentials", ("differential", "punt", "under the radar")),
    ("Defence and clean sheets", ("clean sheet", "defence", "defense", "back line")),
    ("Transfers and hits", ("free transfer", "take a hit", "minus four", "-4 hit")),
]

# How many of a theme's players to name in its title. Three is enough to say
# what it is about; more and the title becomes the list it sits above.
_TITLE_PLAYERS = 3


def theme_title(texts: list[str], players: list[str], creators: int) -> str:
    """A deterministic name for a cluster.

    `texts` is one string per CONTENT row, not per chunk -- the same rule
    `mentions` follows, so a long video split into six chunks cannot outvote
    six creators saying something once.

    Assembled, never written: a matched topic term, the players the cluster's
    stances resolved to, or -- when neither is recognisable -- a plain count,
    which is the honest answer rather than a guess.
    """
    topic = _dominant_topic(texts)
    named = ", ".join(players[:_TITLE_PLAYERS])

    if topic and named:
        return f"{topic} — {named}"
    if topic:
        return topic
    if named:
        return named
    # Nothing recognised. Says what is actually known, which is the count.
    return f"Discussed by {creators} creators"


def _distinctive_players(
    mentions_per_player: dict[str, int], clusters_per_player: dict[str, int]
) -> list[str]:
    """The players who distinguish this cluster from the others.

    Ranked on mentions divided by how many clusters the player appears in, so
    a player discussed everywhere is discounted and one discussed only here is
    promoted. That is the whole trick, and it is deterministic -- no model, no
    corpus statistics beyond the clusters in front of it.

    Needed because raw frequency cannot name a theme. The most-mentioned
    player in a cluster tends to be the most-mentioned player in every
    cluster: measured live on 2026-09-04, Bruno Fernandes led the player list
    of six themes out of eight, so six titles said the same thing.

    Ties break on raw mentions and then alphabetically, so the ordering is
    stable across runs.
    """
    # A player named in a single one of the cluster's content rows is that
    # creator's aside, not the cluster's subject -- and dividing by cluster
    # count actively PROMOTES those, since appearing once is what makes a name
    # rare. Without this floor the ranking surfaced exactly the noise it was
    # meant to demote.
    repeated = {
        name: count for name, count in mentions_per_player.items() if count >= _MIN_TITLE_MENTIONS
    }
    # Nothing was said twice: fall back to the full list rather than returning
    # empty, so theme_title still has something true to work with.
    pool = repeated or mentions_per_player

    return [
        name
        for name, _ in sorted(
            pool.items(),
            key=lambda kv: (
                -kv[1] / max(clusters_per_player.get(kv[0], 1), 1),
                -kv[1],
                kv[0],
            ),
        )
    ]


def _dominant_topic(texts: list[str]) -> str | None:
    """The topic term the most distinct content rows mention, or None.

    Counted per content row rather than per occurrence, so one creator
    saying "wildcard" fifteen times in a monologue counts once. A tie is
    broken by the list order above, which runs most specific first, so
    "double gameweek" wins over "fixtures" on the same evidence.
    """
    folded = [fold(text) for text in texts]

    best: tuple[int, int] | None = None  # (rows matched, -index)
    best_name: str | None = None
    for index, (name, needles) in enumerate(_TOPICS):
        rows = sum(1 for text in folded if any(needle in text for needle in needles))
        if rows == 0:
            continue
        score = (rows, -index)
        if best is None or score > best:
            best, best_name = score, name

    # A term appearing in a single content row is that creator's word, not the
    # cluster's theme. Two is the floor for the same reason MIN_CREATORS is
    # three: a theme of one is just a post.
    if best is None or best[0] < 2:
        return None
    return best_name


# ---------------------------------------------------------------------------
# Choosing which real quote heads a theme.
#
# The seed and the label used to be the same chunk, and they answer different
# questions. The seed is chosen because its neighbourhood spans the most
# creators -- that is what makes the cluster a cross-creator theme rather than
# one person repeating themselves, and it must not change. The label is chosen
# to be *read*, and density says nothing about readability.
#
# Opened in a browser for the first time on 2026-09-04, the panel was headed by
# things like "Whereas with now, right, you have this high floor created for
# you by the variables discussed beforehand." and "Bench one, Fodden, Odigard,
# Brobby, or Cat Lewen." Both are real sentences a real creator really said,
# and neither tells you what the theme is.
#
# So the representative is now picked from among the cluster's MEMBERS -- every
# one of which is a near-neighbour of the seed, so any of them represents the
# cluster -- by how well it reads. Nothing is generated: the label is still a
# quotation with a source_url and a timestamp, which is the whole of ADR-0014's
# requirement. Only the choice between real quotes changed.

# Spoken-transcript filler. Counted rather than stripped: a chunk full of it is
# a worse label than one without, but editing a creator's words to tidy them up
# would make the quotation no longer a quotation.
_FILLER = (
    " um ", " uh ", " erm ", " like ", " you know ", " i mean ", " kind of ",
    " sort of ", " basically ", " obviously ", " literally ", " gonna ",
    " right, ", " so, ", " yeah ",
)

# A label wants to be a sentence, not a fragment and not an essay. Below the
# floor there is nothing to understand; above the ceiling the panel becomes a
# wall of text and `_label` truncates it anyway.
_MIN_CHARS = 60
_MAX_CHARS = 240


def quotability(text: str) -> float:
    """How well a chunk of transcript reads as a theme's heading.

    Pure and score-based rather than a filter, because every candidate is a
    real member of the cluster and one of them has to be chosen -- a filter
    that rejected all of them would leave the theme unlabelled, which is worse
    than a mediocre quote.
    """
    cleaned = " ".join(text.split())
    if not cleaned:
        return -100.0

    lowered = f" {cleaned.lower()} "
    score = 0.0

    # Starts a sentence. The single biggest factor: a chunk beginning
    # mid-clause reads as a transcription error rather than as a quotation,
    # and chunking is done on length, so roughly half of them do.
    if cleaned[0].isupper():
        score += 3.0

    # Ends one too.
    if cleaned.rstrip().endswith((".", "?", "!")):
        score += 1.5

    # In the readable band.
    if _MIN_CHARS <= len(cleaned) <= _MAX_CHARS:
        score += 2.0
    elif len(cleaned) < _MIN_CHARS:
        score -= 2.0

    # Filler density, per hundred characters, so a long chunk is not punished
    # simply for having more room to contain it.
    fillers = sum(lowered.count(token) for token in _FILLER)
    score -= 1.2 * fillers / max(len(cleaned) / 100, 1.0)

    # A run of comma-separated fragments is a list of names being read out
    # ("Bench one, Fodden, Odigard, Brobby, or Cat Lewen"), which is a real
    # sentence and a useless heading.
    commas = cleaned.count(",")
    if commas >= 3 and len(cleaned) / (commas + 1) < 24:
        score -= 3.0

    # Names something. A heading that mentions a proper noun says what the
    # theme is about; one that does not is usually abstract waffle. Detected as
    # a capitalised word that is not sentence-initial, which needs no lookup
    # and so cannot disagree with the players list resolved through stances.
    words = cleaned.split()
    if any(w[:1].isupper() for w in words[1:]):
        score += 1.5

    return score


def _representative(seed_id: int, members: set[int], chunks: dict[int, dict]) -> int:
    """Which member's text heads the theme.

    Falls back to the seed on a tie, so behaviour is deterministic and a
    cluster whose members all score alike is labelled exactly as before.
    """
    return max(
        members,
        key=lambda cid: (quotability(chunks[cid]["chunk_text"]), cid == seed_id, -cid),
    )


def _label(text: str, limit: int = 240) -> str:
    """The medoid chunk, trimmed to something readable.

    Trimmed at a sentence boundary where one is near the limit, so the label
    reads as a quotation rather than as a truncation.
    """
    cleaned = " ".join(text.split())

    # A transcript is chunked on length, not on grammar, so a chunk routinely
    # starts mid-sentence ("in any specific week they're more explosive
    # than..."). Skip to the first sentence boundary so the label reads as the
    # quotation it is. Only when there is one reasonably early -- dropping half
    # a chunk to find a full stop would lose the thing that made it the medoid.
    if cleaned and not cleaned[0].isupper():
        opening = cleaned[:limit]
        start = max(opening.find(". "), opening.find("? "), opening.find("! "))
        if 0 < start < limit // 2:
            cleaned = cleaned[start + 2 :]

    if len(cleaned) <= limit:
        return cleaned
    window = cleaned[:limit]
    stop = max(window.rfind(". "), window.rfind("? "), window.rfind("! "))
    if stop > limit // 2:
        return window[: stop + 1]
    return window.rsplit(" ", 1)[0] + "..."


def build_themes(
    conn: Connection,
    team_id: int,
    threshold: float = DEFAULT_THRESHOLD,
    limit: int = DEFAULT_LIMIT,
    min_creators: int = MIN_CREATORS,
    now: datetime | None = None,
) -> ThemeBoard:
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=WINDOW_DAYS)
    planning = resolve_planning_gameweek(conn, team_id)

    with conn.cursor() as cur:
        cur.execute(_SCOPE_SQL, {"since": since, "narration": _NARRATION})
        chunks = {row["id"]: dict(row) for row in cur.fetchall()}

    # Guarded on having any content at all, NOT on `min_creators`: those are
    # different questions, and conflating them made "no group of 99 creators
    # agrees" report as "run fpl-ingest-creators". A corpus that exists and a
    # corpus that agrees are separate facts and the notes must not swap them.
    if not chunks:
        return ThemeBoard(
            planning_gameweek=planning,
            chunks_considered=0,
            threshold=threshold,
            note=(
                "No creator content in the window to find themes in. Run "
                "fpl-ingest-creators."
            ),
        )

    graph = _neighbours(conn, since, threshold)
    clusters = _cluster(chunks, graph, min_creators)

    content_ids = sorted({chunk["content_id"] for chunk in chunks.values()})
    with conn.cursor() as cur:
        cur.execute(_PLAYERS_SQL, (content_ids,))
        players_by_content: dict[int, set[str]] = defaultdict(set)
        for row in cur.fetchall():
            players_by_content[row["source_content_id"]].add(row["player_name"])

        cur.execute(_RESOLVED_PLAYERS_SQL, (content_ids,))
        resolved_by_content: dict[int, set[str]] = defaultdict(set)
        for row in cur.fetchall():
            resolved_by_content[row["source_content_id"]].add(row["player_name"])

    # Per-cluster player counts, computed for every cluster BEFORE any theme is
    # built, because naming a theme needs to know what the other themes are
    # about. See _distinctive_players.
    mentions_by_cluster: list[dict[str, int]] = []
    resolved_by_cluster: list[dict[str, int]] = []
    for _, members in clusters:
        content_ids_here = {chunks[m]["content_id"] for m in members}
        counts: dict[str, int] = defaultdict(int)
        resolved: dict[str, int] = defaultdict(int)
        for content_id in content_ids_here:
            for name in players_by_content.get(content_id, set()):
                counts[name] += 1
            for name in resolved_by_content.get(content_id, set()):
                resolved[name] += 1
        mentions_by_cluster.append(counts)
        resolved_by_cluster.append(resolved)

    # How many clusters each player turns up in at all. A player in most of
    # them is not evidence about any single one.
    clusters_per_player: dict[str, int] = defaultdict(int)
    for counts in resolved_by_cluster:
        for name in counts:
            clusters_per_player[name] += 1

    recent_cutoff = now - timedelta(hours=RECENT_HOURS)
    themes: list[Theme] = []
    for index, (seed_id, members) in enumerate(clusters):
        rows = [chunks[m] for m in members]
        creator_names = sorted({row["creator_name"] for row in rows})
        published = [row["published_at"] for row in rows if row["published_at"] is not None]
        mentions_per_player = mentions_by_cluster[index]
        # Ranked by how many of the cluster's content rows mention them, so a
        # theme names what it keeps returning to rather than every player who
        # appears once in a forty-minute transcript.
        players = [
            name
            for name, _ in sorted(
                mentions_per_player.items(), key=lambda kv: (-kv[1], kv[0])
            )[:MAX_PLAYERS]
        ]
        # A DIFFERENT ranking for the title, and the two answer different
        # questions. `players` above says who this theme discusses, which is
        # what the panel's list is for. A title has to say what makes this
        # theme not the others -- and by raw frequency it could not, because
        # the most-mentioned player is the most-mentioned player everywhere.
        # Live on 2026-09-04, "Bruno Fernandes" headed six of eight themes.
        title_players = _distinctive_players(
            resolved_by_cluster[index], clusters_per_player
        )

        # The seed defines the cluster; a member is chosen to head it. See
        # _representative -- density and readability are different questions,
        # and the seed only ever answered the first.
        quote_id = _representative(seed_id, members, chunks)

        # One text per CONTENT row, not per chunk, so a long transcript split
        # into six chunks cannot outvote six creators saying something once --
        # the same rule `mentions` follows below.
        text_by_content = {row["content_id"]: row["chunk_text"] for row in rows}

        themes.append(
            Theme(
                title=theme_title(
                    list(text_by_content.values()), title_players, len(creator_names)
                ),
                label=_label(chunks[quote_id]["chunk_text"]),
                creators=len(creator_names),
                creator_names=creator_names,
                # Mentions counts the CONTENT rows, not the chunks: one long
                # video split into six chunks is one mention, and counting
                # chunks would make verbosity look like consensus.
                mentions=len({row["content_id"] for row in rows}),
                players=players,
                # The citation must point at the quote actually shown, not at
                # the seed -- a link to different words than the ones above it
                # is precisely the misattribution ADR-0014 exists to stop.
                citation=_citation(chunks[quote_id]),
                is_new=bool(published) and min(published) >= recent_cutoff,
                first_seen=min(published).isoformat() if published else None,
            )
        )

    # Tie-broken on the title rather than the quote: the title is the theme's
    # stable name, while which quote heads it can change as chunks arrive.
    themes.sort(key=lambda theme: (-theme.creators, -theme.mentions, theme.title))
    return ThemeBoard(
        planning_gameweek=planning,
        themes=themes[:limit],
        chunks_considered=len(chunks),
        threshold=threshold,
        note=None
        if themes
        else (
            f"No group of {min_creators} or more creators is saying the same thing "
            "right now. That is a real answer, not a gap."
        ),
    )
