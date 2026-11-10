import os
from typing import Protocol

import anthropic

_RECORD_STANCES_TOOL = {
    "name": "record_stances",
    "description": (
        "Record every distinct FPL player stance expressed in the given content. "
        "Call with an empty list if the content expresses no stance on any player."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "stances": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "player_name": {"type": "string"},
                        "stance": {
                            "type": "string",
                            "enum": ["buy", "sell", "hold", "captain"],
                        },
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                        "reason": {"type": "string"},
                    },
                    "required": ["player_name", "stance", "confidence", "reason"],
                },
            }
        },
        "required": ["stances"],
    },
}


class StanceExtractor(Protocol):
    def extract(self, content_text: str) -> list[dict]: ...


class ClaudeStanceExtractor:
    """Layer 2 (ADR-0005): converts one piece of creator content into zero or
    more structured Creator Stance drafts. Forces a single `record_stances`
    tool call rather than parsing prose, same tool-forcing pattern
    ClaudeReasoning uses for submit_recommendation (ADR-0004) -- deterministic
    shape in, even though the specific stances judged are still the model's
    call."""

    def __init__(self, model: str | None = None) -> None:
        self._client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        self._model = model or os.environ.get("REASONING_MODEL", "claude-sonnet-5")

    def extract(self, content_text: str) -> list[dict]:
        response = self._client.messages.create(
            model=self._model,
            max_tokens=1024,
            tool_choice={"type": "tool", "name": "record_stances"},
            tools=[_RECORD_STANCES_TOOL],
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Extract every distinct FPL player stance (buy/sell/hold/captain) "
                        "expressed in this content. A single piece of content may name "
                        "more than one player.\n\nContent:\n" + content_text
                    ),
                }
            ],
        )
        tool_use = next(b for b in response.content if b.type == "tool_use")
        return tool_use.input["stances"]


class FakeStanceExtractor:
    """Deterministic test double -- returns the same canned stances for every
    call, and records what it was called with for assertion."""

    def __init__(self, stances: list[dict] | None = None) -> None:
        self._stances = stances if stances is not None else []
        self.calls: list[str] = []

    def extract(self, content_text: str) -> list[dict]:
        self.calls.append(content_text)
        return self._stances
