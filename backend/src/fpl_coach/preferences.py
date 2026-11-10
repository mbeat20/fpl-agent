"""ADR-0010: Preferences are a single flat text file, not a memory system."""

import os
import re
from pathlib import Path

from fpl_coach.fpl_rules import FPL_DOMAIN_BRIEFING

DEFAULT_PATH = Path(__file__).resolve().parent.parent.parent / "preferences.md"

_REMEMBER_RE = re.compile(r"^\s*remember\s*:\s*(.+)$", re.IGNORECASE | re.DOTALL)

_SYSTEM_PROMPT_HEADER = "Standing user preferences — apply these to every Recommendation:\n\n"


def _preferences_path() -> Path:
    return Path(os.environ.get("PREFERENCES_PATH", DEFAULT_PATH))


def load_preferences() -> str:
    path = _preferences_path()
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def append_preference(text: str) -> None:
    path = _preferences_path()
    with path.open("a", encoding="utf-8") as f:
        f.write(f"- {text.strip()}\n")


def build_system_prompt(preferences: str) -> str:
    """The FPL domain briefing, plus the user's Preferences when they have any.

    The briefing comes first and is byte-identical every call, so the whole
    system prompt stays cacheable up to the point where the (rarely changing)
    Preferences begin. Never empty now -- an unset preferences.md drops the
    Preferences section, not the rules of the game."""
    if not preferences:
        return FPL_DOMAIN_BRIEFING
    return FPL_DOMAIN_BRIEFING + "\n" + _SYSTEM_PROMPT_HEADER + preferences


def extract_remember_instruction(question: str) -> str | None:
    """`None` unless `question` is a "remember: X" instruction (ADR-0010)."""
    match = _REMEMBER_RE.match(question)
    if not match:
        return None
    text = match.group(1).strip()
    return text or None
