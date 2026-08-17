from codex_lamp.config import load_settings
from codex_lamp.question import requests_input


def test_detects_terminal_questions_and_confirmations(tmp_path):
    markers = load_settings(tmp_path).question_markers
    assert requests_input("你确认这个方案吗？", markers)
    assert requests_input("Please confirm the migration plan.", markers)
    assert requests_input("Would you like me to continue?", markers)


def test_normal_completion_is_not_input(tmp_path):
    markers = load_settings(tmp_path).question_markers
    assert not requests_input("Implementation is complete and all tests pass.", markers)
    assert not requests_input(None, markers)


def test_ignores_blank_configured_markers():
    assert not requests_input("Implementation is complete and all tests pass.", ("", "   "))
