import logging
import os
from typing import Protocol

import anthropic

# Issue 16: extraction runs on every piece of content on every creator, so it
# is the one Claude call whose volume scales with the roster. A 6,000-word
# transcript per creator per day on Sonnet is not a sensible recurring bill,
# and stance extraction is a structured-output task rather than a reasoning
# one -- so this is pinned to Haiku independently of REASONING_MODEL, which
# it used to inherit.
DEFAULT_EXTRACTION_MODEL = "claude-haiku-4-5-20251001"

_log = logging.getLogger(__name__)

# Haiku 4.5 will not create a cache entry below a 4096-token prefix, and the
# tool schema plus instructions below come to roughly a quarter of that -- so
# the two cache_control markers in `extract` are inert, not broken. They cost
# nothing and are left in place because they become live the moment either the
# schema grows past the floor or the model changes to one with a lower one
# (Opus 5 caches from 512). What actually keeps this call cheap is Haiku plus
# one call per piece of content; the log line below is how a roster that grows
# into a real bill becomes visible before the invoice does.

# Issue 16: buy/sell/hold/captain could not express a transcript's most
# valuable claims. avoid/differential/bench are still about one player;
# chip_timing and fixture_view legitimately name none, which is why
# player_name is no longer required (schema.sql).
STANCE_VOCABULARY = [
    "buy",
    "sell",
    "hold",
    "captain",
    "avoid",
    "differential",
    "bench",
    "chip_timing",
    "fixture_view",
]

_RECORD_STANCES_TOOL = {
    "name": "record_stances",
    "description": (
        "Record every distinct FPL stance expressed in the given content. "
        "Call with an empty list if the content expresses no stance at all."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "stances": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "player_name": {
                            "type": "string",
                            "description": (
                                "the player the stance is about. Omit entirely for a "
                                "chip_timing or fixture_view stance, which is not about "
                                "a player -- do not put a club or a chip name here."
                            ),
                        },
                        "subject": {
                            "type": "string",
                            "description": (
                                "what a non-player stance is about: the club for a "
                                "fixture_view, the chip for a chip_timing. Omit for a "
                                "player stance."
                            ),
                        },
                        "stance": {"type": "string", "enum": STANCE_VOCABULARY},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                        "reason": {"type": "string"},
                        "start_seconds": {
                            "type": "integer",
                            "description": (
                                "for timestamped content, the [MM:SS] marker the claim "
                                "was made at, in seconds. Omit if the content carries no "
                                "timestamps."
                            ),
                        },
                    },
                    "required": ["stance", "confidence", "reason"],
                },
            }
        },
        "required": ["stances"],
    },
}

_INSTRUCTIONS = (
    "Extract every distinct FPL stance expressed in this content. One piece of content "
    "may express many, about different players.\n\n"
    "Player stances (buy, sell, hold, captain, avoid, differential, bench) need a "
    "player_name. A differential stance means the creator is backing a low-owned player; "
    "avoid means actively steer clear, which is stronger than not buying.\n\n"
    "Non-player stances name no player: chip_timing (when to play wildcard, bench boost, "
    "triple captain, free hit) and fixture_view (a club's upcoming run being good or bad). "
    "Put the club or the chip in `subject` instead.\n\n"
    "Only record what the creator actually asserts. Do not infer a stance from a player "
    "merely being mentioned, and do not invent a confidence the content does not support."
)

_TIMESTAMP_NOTE = (
    "\n\nThe content below is a video transcript with [MM:SS] markers. Set start_seconds "
    "on each stance to the marker nearest where the claim is made, so it can be cited as "
    "a deep link."
)


class StanceExtractor(Protocol):
    def extract(self, content_text: str, timestamped: bool = False) -> list[dict]: ...


class ClaudeStanceExtractor:
    """Layer 2 (ADR-0005): converts one piece of creator content into zero or
    more structured Creator Stance drafts. Forces a single `record_stances`
    tool call rather than parsing prose, same tool-forcing pattern
    ClaudeReasoning uses for submit_recommendation (ADR-0004) -- deterministic
    shape in, even though the specific stances judged are still the model's
    call.

    Issue 16: a transcript is passed whole, in one call, rather than chunk by
    chunk. Chunking it would both cost more and lose the claims that span the
    video ("as I said about Arsenal earlier"), and the chunks exist for
    pgvector retrieval, not for extraction.
    """

    def __init__(self, model: str | None = None) -> None:
        self._client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        self._model = model or os.environ.get("EXTRACTION_MODEL", DEFAULT_EXTRACTION_MODEL)

    def extract(self, content_text: str, timestamped: bool = False) -> list[dict]:
        instructions = _INSTRUCTIONS + (_TIMESTAMP_NOTE if timestamped else "")
        response = self._client.messages.create(
            model=self._model,
            # A 6,000-word transcript can legitimately yield a couple of dozen
            # stances; the old 1024 truncated the tool call mid-array.
            max_tokens=8192,
            tool_choice={"type": "tool", "name": "record_stances"},
            # The tool schema and instructions are identical on every call and
            # sit ahead of the content, so they cache across a whole run.
            tools=[{**_RECORD_STANCES_TOOL, "cache_control": {"type": "ephemeral"}}],
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": instructions,
                            "cache_control": {"type": "ephemeral"},
                        },
                        {"type": "text", "text": "Content:\n" + content_text},
                    ],
                }
            ],
        )
        usage = response.usage
        _log.info(
            "stance extraction: in=%d cache_write=%d cache_read=%d out=%d (%d chars)",
            usage.input_tokens,
            usage.cache_creation_input_tokens or 0,
            usage.cache_read_input_tokens or 0,
            usage.output_tokens,
            len(content_text),
        )
        tool_use = next(b for b in response.content if b.type == "tool_use")
        return [_normalise(stance) for stance in tool_use.input["stances"]]


def _normalise(stance: dict) -> dict:
    """Fills the optional keys the pipeline reads, so a caller never has to
    guess whether the model omitted one. A player stance that arrived with no
    player_name is dropped by the pipeline, not silently rewritten here."""
    return {
        "player_name": (stance.get("player_name") or None),
        "subject": (stance.get("subject") or None),
        "stance": stance["stance"],
        "confidence": stance["confidence"],
        "reason": stance["reason"],
        "start_seconds": stance.get("start_seconds"),
    }


class FakeStanceExtractor:
    """Deterministic test double -- returns the same canned stances for every
    call, and records what it was called with for assertion."""

    def __init__(self, stances: list[dict] | None = None) -> None:
        self._stances = stances if stances is not None else []
        self.calls: list[str] = []

    def extract(self, content_text: str, timestamped: bool = False) -> list[dict]:
        self.calls.append(content_text)
        return [_normalise(stance) for stance in self._stances]
