from fpl_coach.preferences import (
    append_preference,
    build_system_prompt,
    extract_remember_instruction,
    load_preferences,
)


def test_load_preferences_returns_empty_string_when_file_absent(tmp_path, monkeypatch):
    monkeypatch.setenv("PREFERENCES_PATH", str(tmp_path / "does-not-exist.md"))

    assert load_preferences() == ""


def test_append_preference_creates_file_and_load_reads_it_back(tmp_path, monkeypatch):
    monkeypatch.setenv("PREFERENCES_PATH", str(tmp_path / "preferences.md"))

    append_preference("Dislikes knee-jerk transfers")
    append_preference("Takes captain risks when chasing rank")

    assert load_preferences() == (
        "- Dislikes knee-jerk transfers\n- Takes captain risks when chasing rank"
    )


def test_build_system_prompt_is_the_domain_briefing_when_no_preferences():
    """No longer empty: the rules of the game are not optional, only the
    user's Preferences section is. An empty system prompt used to leave the
    reasoning layer with no statement of squad or transfer legality at all,
    which is how illegal cross-position transfers reached the user."""
    system = build_system_prompt("")

    assert "like-for-like" in system
    assert "2 GK, 5 DEF, 5 MID, 3 FWD" in system


def test_build_system_prompt_includes_preferences_text():
    system = build_system_prompt("- Dislikes knee-jerk transfers")

    assert "Dislikes knee-jerk transfers" in system
    # Briefing first, preferences after: the briefing is byte-identical on
    # every call, so it stays inside the cacheable prefix of the prompt.
    assert system.index("like-for-like") < system.index("Dislikes knee-jerk")


def test_extract_remember_instruction_matches_prefix():
    assert (
        extract_remember_instruction("remember: I dislike knee-jerk transfers")
        == "I dislike knee-jerk transfers"
    )
    assert extract_remember_instruction("Remember:   spaced out   ") == "spaced out"


def test_extract_remember_instruction_ignores_other_questions():
    assert extract_remember_instruction("Who should I captain?") is None
    assert extract_remember_instruction("give me the safest pick this week") is None
