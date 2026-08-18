import os
from dataclasses import dataclass
from typing import Any

import anthropic
from pydantic import BaseModel

from fpl_coach.models import (
    Alternative,
    Player,
    PlayerComparison,
    PriceChangePredictions,
    Recommendation,
    RecommendationPick,
    Team,
    TransferComparison,
)
from fpl_coach.reasoning.base import ToolSpec

_MAX_TURNS = 10


@dataclass(frozen=True)
class _KnownPlayer:
    """Common id/name shape for anything a tool result can make a valid
    submit_recommendation pick — a full Player, or just a player_id/name
    pair from a lighter-weight result like PriceChangePredictions."""

    id: int
    name: str


class ClaudeReasoning:
    """ADR-0003's Claude-backed Reasoning implementation.

    ADR-0004: the primary pick must be deterministic, not sampled prose.
    Rather than parsing a player name out of the model's text, the model is
    forced to finalize through a `submit_recommendation` tool whose player
    ids are constrained to an enum of players it has actually seen data for
    — the squad (from `get_my_team`, seeded up front) plus whatever
    `get_player`/`compare_players` results came back during the
    conversation (issue 05: the pick may be a player outside the squad,
    e.g. "is Bukayo Saka worth buying?", but only once the model has looked
    them up). The returned Recommendation's pick is always built from that
    authoritative lookup, never from whatever name the model happened to
    write — creativity stays confined to confidence, evidence, risks, and
    the alternative's reasoning.
    """

    def __init__(self, model: str | None = None) -> None:
        self._client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        self._model = model or os.environ.get("REASONING_MODEL", "claude-sonnet-5")

    def reason(self, prompt: str, tools: list[ToolSpec], system: str = "") -> Recommendation:
        get_my_team = next(t for t in tools if t.name == "get_my_team")
        known_players: dict[int, _KnownPlayer] = {
            p.id: _KnownPlayer(id=p.id, name=p.name) for p in get_my_team.handler().players
        }

        anthropic_tools = [
            {"name": t.name, "description": t.description, "input_schema": t.input_schema}
            for t in tools
        ]
        handlers = {t.name: t.handler for t in tools}

        messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
        system_kwargs = {"system": system} if system else {}

        for _ in range(_MAX_TURNS):
            response = self._client.messages.create(
                model=self._model,
                max_tokens=1024,
                tools=anthropic_tools + [self._build_submit_tool(known_players)],
                tool_choice={"type": "any"},
                messages=messages,
                **system_kwargs,
            )
            messages.append({"role": "assistant", "content": response.content})

            tool_uses = [block for block in response.content if block.type == "tool_use"]
            if not tool_uses:
                raise RuntimeError("Claude finished without calling submit_recommendation")

            submission = next(
                (b for b in tool_uses if b.name == "submit_recommendation"), None
            )
            if submission is not None:
                return self._build_recommendation(submission.input, known_players)

            tool_results = []
            for block in tool_uses:
                try:
                    result = handlers[block.name](**block.input)
                except Exception as exc:  # noqa: BLE001 - surfaced to Claude, not raised
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": str(exc),
                            "is_error": True,
                        }
                    )
                    continue

                self._record_known_players(result, known_players)
                content = (
                    result.model_dump_json() if isinstance(result, BaseModel) else str(result)
                )
                tool_results.append(
                    {"type": "tool_result", "tool_use_id": block.id, "content": content}
                )
            messages.append({"role": "user", "content": tool_results})

        raise RuntimeError("Claude did not call submit_recommendation within the turn budget")

    @staticmethod
    def _record_known_players(result: Any, known_players: dict[int, _KnownPlayer]) -> None:
        if isinstance(result, Player):
            known_players[result.id] = _KnownPlayer(id=result.id, name=result.name)
        elif isinstance(result, (Team, PlayerComparison)):
            for p in result.players:
                known_players[p.id] = _KnownPlayer(id=p.id, name=p.name)
        elif isinstance(result, PriceChangePredictions):
            for p in result.rising + result.falling:
                known_players[p.player_id] = _KnownPlayer(id=p.player_id, name=p.player_name)
        elif isinstance(result, TransferComparison):
            for option in (result.player_out, result.player_in):
                known_players[option.player.id] = _KnownPlayer(
                    id=option.player.id, name=option.player.name
                )

    @staticmethod
    def _build_submit_tool(known_players: dict[int, _KnownPlayer]) -> dict:
        player_ids = sorted(known_players)
        return {
            "name": "submit_recommendation",
            "description": (
                "Finalize the Recommendation for the user's question. Only players "
                "already returned by get_my_team, get_player, compare_players, or "
                "predict_price_changes in this conversation are valid picks — look a "
                "player up before recommending them."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "pick_player_id": {
                        "type": "integer",
                        "enum": player_ids,
                        "description": "id of the player being recommended",
                    },
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "evidence": {"type": "array", "items": {"type": "string"}},
                    "risks": {"type": "array", "items": {"type": "string"}},
                    "alternative_player_id": {"type": "integer", "enum": player_ids},
                    "alternative_reason": {"type": "string"},
                },
                "required": [
                    "pick_player_id",
                    "confidence",
                    "evidence",
                    "risks",
                    "alternative_player_id",
                    "alternative_reason",
                ],
            },
        }

    @staticmethod
    def _build_recommendation(
        args: dict[str, Any], known_players: dict[int, _KnownPlayer]
    ) -> Recommendation:
        pick_player = known_players[args["pick_player_id"]]
        alt_player = known_players[args["alternative_player_id"]]
        if alt_player.id == pick_player.id:
            alt_player = next(p for p in known_players.values() if p.id != pick_player.id)

        return Recommendation(
            pick=RecommendationPick(player_id=pick_player.id, player_name=pick_player.name),
            confidence=args["confidence"],
            evidence=args["evidence"],
            risks=args["risks"],
            alternative=Alternative(
                player_id=alt_player.id,
                player_name=alt_player.name,
                reason=args["alternative_reason"],
            ),
        )
