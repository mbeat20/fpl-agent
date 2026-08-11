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


def test_build_system_prompt_is_empty_when_no_preferences():
    assert build_system_prompt("") == ""


def test_build_system_prompt_includes_preferences_text():
    system = build_system_prompt("- Dislikes knee-jerk transfers")

    assert "Dislikes knee-jerk transfers" in system


def test_extract_remember_instruction_matches_prefix():
    assert (
        extract_remember_instruction("remember: I dislike knee-jerk transfers")
        == "I dislike knee-jerk transfers"
    )
    assert extract_remember_instruction("Remember:   spaced out   ") == "spaced out"


def test_extract_remember_instruction_ignores_other_questions():
    assert extract_remember_instruction("Who should I captain?") is None
    assert extract_remember_instruction("give me the safest pick this week") is None
